import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from typing import Any, Dict, List, Optional, Sequence

import audit
import auth
import db
import documents
import evidence
import rag
from admin import router as admin_router
from ahp import derive_criteria_weights
from consistency_repair import suggest_minimal_revision
from config import get_settings
from domain import (
    EXCLUDED_NON_DISCRIMINATIVE,
    analyze_sensitivity,
    build_comparability_set,
    compute_scores,
    get_methodology,
    indicators_for,
    renormalize_weights,
    weights_from_answers,
)
from guardrails import (
    GuardrailLog,
    GuardrailRejection,
    enforce_document_quota,
    resolve_within,
    validate_upload,
)
from llm.prompts import registered_versions
import query_refinement
from preferences import explain_preferences, sanitize_qa_pairs
from reporting import build_synthesis, dimension_performance
from providers_data import PROVIDERS, PROVIDER_SCORES_PROVENANCE
from rag.metadata import SCOPE_GLOBAL, evaluation_scope
from schemas import CRITERIA, QuestionnaireResponse, RecommendationResponse

logger = logging.getLogger("uvicorn.error")

# Estados de uma avaliação (§26). O ranking só é considerado íntegro em
# COMPLETED; COMPLETED_WITH_LIMITATIONS registra que algo faltou sem transformar
# a falha em pontuação presumida.
STATUS_COMPLETED = "COMPLETED"
STATUS_COMPLETED_WITH_LIMITATIONS = "COMPLETED_WITH_LIMITATIONS"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Cria/atualiza o banco de auditoria (idempotente) e confere a configuração."""
    db.init_db()
    settings = get_settings()
    if not auth.is_configured():
        logger.warning(
            "ADMIN_PASSWORD não definido: a área de gestão (/api/admin) fica indisponível."
        )
    fingerprint = audit.questionnaire_fingerprint()
    if fingerprint.get("questions_hash") is None:
        logger.warning(
            "questions.json não pôde ser lido em %s (%s): as avaliações ficarão sem "
            "hash de versão do questionário.",
            fingerprint.get("questions_source"),
            fingerprint.get("unavailable_reason"),
        )
    logger.info(
        "LLM=%s/%s%s · embeddings=%s/%s · algoritmo v%s",
        settings.llm_provider,
        settings.llm_model,
        "".join(f" → {p.provider}/{p.model}" for p in settings.llm_fallbacks),
        settings.embedding_provider,
        settings.embedding_model,
        settings.scoring_algorithm_version,
    )
    if not settings.llm_fallbacks:
        logger.info(
            "Sem provedor de fallback: no limite de taxa a extração espera, e no "
            "fim da espera a dimensão fica sem evidência. Configure "
            "LLM_FALLBACK_PROVIDERS e a chave correspondente para ter alternativa."
        )
    yield


app = FastAPI(title="Cloud Provider Selector API", lifespan=lifespan)
app.include_router(admin_router)

# Em produção o nginx do frontend faz proxy same-origin para /api (CORS nem entra
# em jogo). Isso aqui existe só para permitir `npm run dev` (Vite) local direto
# contra o backend durante desenvolvimento.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:8501").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Diretórios da base documental e o pipeline de ingestão vivem em `documents`:
# a área de gestão (`admin.py`) executa a mesma ingestão global, e este módulo
# importa o router dela — a função compartilhada não pode morar aqui.
#
#   data/pdf                   documentos do administrador, usados em toda busca RAG
#   data/upload/<session_id>   anexos de uma avaliação, usados só na sessão ativa


def _inconsistency_detail(ahp_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Corpo do 409 quando os julgamentos do bloco D se contradizem (§4.2.3).

    Traz o que a interface precisa para *solicitar a revisão* em vez de só
    recusar: a razão medida, o limite, as perguntas a rever e — quando há — o par
    cujo julgamento mais destoa dos demais. Sem isso o gestor recebe "revise suas
    comparações" e três perguntas idênticas para escolher.

    O diagnóstico aponta e propõe; não corrige. A dissertação atribui a revisão ao
    decisor, e um sistema que ajustasse o julgamento sozinho estaria fabricando a
    preferência que ele deveria estar coletando — por isso `suggestion` viaja como
    proposta, e só passa a valer se o gestor a adotar na tela e reenviar. O que
    fica no registro de auditoria é sempre o que ele enviou.

    A proposta existe porque recusar sem indicar saída pode ser um beco: com três
    dimensões, há combinações de duas respostas para as quais **nenhuma** terceira
    resposta da escala verbal fecha o CR (ver `consistency_repair`). Nesses casos
    "revise as comparações" manda o gestor procurar uma resposta que não existe.
    """
    julgamentos = ahp_result.get("judgments") or {}
    return {
        "error": "AHP_INCONSISTENT_JUDGMENTS",
        "message": (
            "As comparações do bloco D se contradizem entre si. Revise-as antes de "
            "prosseguir: enquanto a razão de consistência estiver acima do limite, os "
            "pesos das dimensões não representam uma ordem de prioridade coerente."
        ),
        "consistency_ratio": ahp_result.get("consistency_ratio"),
        "consistency_threshold": ahp_result.get("consistency_threshold"),
        "consistency_index": ahp_result.get("consistency_index"),
        "lambda_max": ahp_result.get("lambda_max"),
        # Perguntas do questionário a revisar, para a interface levar o gestor até elas.
        "question_ids": [
            j.get("question_id") for j in julgamentos.values() if j.get("question_id")
        ],
        "judgments": {
            k: {"choice": j.get("choice"), "question_id": j.get("question_id")}
            for k, j in julgamentos.items()
        },
        "worst_pair": ahp_result.get("worst_pair"),
        # Menor alteração da escala verbal que traz o CR para dentro do limite.
        # É sugestão, não correção: nada aqui foi aplicado ao envio.
        "suggestion": suggest_minimal_revision(ahp_result),
    }


@app.post("/api/recommend", response_model=RecommendationResponse)
async def recommend(q: QuestionnaireResponse):
    """
    Recebe respostas do gestor, processa com LLM + RAG e AHP,
    e retorna ranking de provedores com justificativas e evidências.
    """
    # Coletor de eventos de guardrail desta avaliação (§27). Acompanha a
    # requisição inteira e é gravado junto do envio.
    guardrail_log = GuardrailLog()
    llm_runs: List[Dict[str, Any]] = []
    rag_audit: List[Dict[str, Any]] = []

    # 1) Comparações par a par (seção D) → matriz de julgamentos do gestor.
    #    É a única fonte dos pesos entre dimensões. O gestor informa a dimensão
    #    prioritária e a intensidade verbal; a razão de Saaty correspondente é
    #    derivada aqui no servidor (pairwise.py), nunca aceita pronta do cliente.
    judgments = q.pairwise_judgments()

    # 2) Perguntas de relevância (A, B, C) → média 1-5 por dimensão.
    #    Mede a relevância dos indicadores dentro de cada dimensão; não é
    #    convertida para a escala de Saaty (são conceitos distintos), então entra
    #    na memória de cálculo e no contexto do LLM, não nos pesos.
    relevance = q.relevance_by_criterion()

    # 3) AHP: matriz par a par → matriz normalizada → pesos + consistência.
    #    O método (§6.3) e o limite de CR vêm da configuração metodológica.
    ahp_result = derive_criteria_weights(judgments, CRITERIA)
    criteria_weights = ahp_result["weights"]

    # 3a) Porta da consistência (§4.2.3). A dissertação é explícita: "Caso o valor
    #     de CR seja superior a 0,10, o sistema informa ao usuário a existência de
    #     inconsistência nos julgamentos e solicita a revisão das comparações
    #     ANTES DO PROSSEGUIMENTO do processo de avaliação". E logo adiante: "Uma
    #     vez verificada a consistência da matriz de julgamentos, os pesos
    #     relativos [...] podem ser utilizados na etapa subsequente".
    #
    #     Por isso a verificação vem aqui, antes de qualquer chamada à LLM: pesos
    #     que se contradizem não devem produzir ranking, e gastar a extração
    #     documental sobre eles seria pagar caro por um resultado que o próprio
    #     método declara não utilizável.
    if not ahp_result.get("is_consistent", True):
        raise HTTPException(status_code=409, detail=_inconsistency_detail(ahp_result))

    # 3b) Pesos dos indicadores (§5.1 e §7). A partir daqui os blocos A/B/C têm
    #     destino metodológico: relevância → coeficiente → peso local → peso
    #     global (peso da dimensão × peso local). Indicador sem coeficiente
    #     válido fica sem peso — nunca com zero, que afirmaria irrelevância.
    methodology = get_methodology()
    weight_set = weights_from_answers(q.answers_by_question(), criteria_weights, methodology)

    # 4) Guardrails de entrada sobre o texto do gestor, uma vez só (§23.2–§23.5).
    #    O resultado abastece os dois consumidores desse texto — a justificativa
    #    e o refinamento das consultas —, para que a mesma credencial não seja
    #    registrada duas vezes no log de auditoria.
    try:
        safe_qa_pairs = sanitize_qa_pairs(q.qa_for_llm(), guardrail_log)
    except GuardrailRejection as exc:
        raise HTTPException(status_code=422, detail=exc.event.reason) from exc

    # 4a) LLM redige a justificativa a partir das respostas dissertativas e dos
    #     pesos já calculados. Ela não altera nenhum número: o cálculo permanece
    #     determinístico e reprodutível a partir das respostas fechadas.
    notes, llm_run = await explain_preferences(
        qa_pairs=safe_qa_pairs,
        relevance=relevance,
        criteria_weights=criteria_weights,
    )
    llm_runs.append(llm_run.as_dict())

    # 5) Só entram no ranking os provedores com base documental indexada.
    #
    #    DESVIO ASSUMIDO em relação à §4.4.1.3. A diretriz trata sempre do
    #    indicador — "esse indicador é considerado não avaliável naquela
    #    execução" — e em nenhum ponto prevê retirar uma alternativa da
    #    comparação. Ela também não define como o conjunto de alternativas é
    #    formado, de modo que este filtro preenche um silêncio em vez de
    #    contradizer o texto.
    #
    #    A alternativa literal foi avaliada e descartada pelo autor: manter o
    #    provedor sem documentos deixaria todos os seus indicadores em ausência
    #    de evidência, o que os retiraria da comparação para TODAS as
    #    alternativas (§11.1) e colapsaria o ranking em zeros sempre que um dos
    #    provedores não tivesse base indexada.
    #
    #    A exclusão não é silenciosa: os provedores fora aparecem em
    #    `coverage.excluded_no_documents` e a avaliação recebe uma limitação
    #    declarada.
    chunk_counts = await run_in_threadpool(rag.count_chunks_by_provider)
    evaluated = [p for p in PROVIDERS if chunk_counts.get(p["id"], 0) > 0]
    excluded = [
        {"id": p["id"], "name": p["name"]}
        for p in PROVIDERS
        if chunk_counts.get(p["id"], 0) == 0
    ]

    if not evaluated:
        raise HTTPException(
            status_code=409,
            detail=(
                "Nenhum provedor possui documentos indexados. Faça a ingestão em "
                "data/pdf (área de gestão → base documental) antes de gerar uma recomendação."
            ),
        )

    # 6) Evidências documentais → desempenho por indicador (§4.4.1).
    #    O RAG consulta uma vez por (provedor × indicador), usando os termos do
    #    Quadro 27 que vivem em `indicators.json`; a LLM interpreta os trechos
    #    sob o prompt do Quadro 26 e devolve valor publicado ou categoria de
    #    rubrica. Ela não atribui nota: a conversão em número é da rubrica, e a
    #    normalização vem no passo seguinte.
    session_id = getattr(q, "session_id", None) or None
    weighted_indicators = indicators_for(weight_set, methodology)

    # 6a) Bloco E → refinamento das consultas (§4.5.1). Os requisitos
    #     institucionais descritos pelo gestor viram termos de busca associados
    #     aos indicadores já definidos. Eles direcionam a recuperação e nada
    #     mais: os pesos das dimensões e os pesos locais já foram calculados
    #     acima, a partir dos blocos A–D, e não são revisitados.
    #     O Bloco D não entra aqui. Ele é a elicitação par a par que o AHP
    #     converte em peso, e a §4.5.1 reserva o direcionamento da recuperação ao
    #     Bloco E. Enquanto as comparações viajavam neste texto, mudar apenas a
    #     prioridade declarada mudava os termos refinados, os trechos recuperados
    #     e, por consequência, a evidência extraída — o peso do gestor escolhendo
    #     quais documentos seriam lidos.
    rotulos_bloco_d = q.pairwise_question_labels()
    qa_para_recuperacao = [
        pair for pair in safe_qa_pairs if pair.get("pergunta") not in rotulos_bloco_d
    ]

    query_hints, refinement_run = await query_refinement.refine_queries(
        qa_pairs=qa_para_recuperacao,
        indicators=weighted_indicators,
        guardrail_log=guardrail_log,
    )
    if refinement_run is not None:
        llm_runs.append(refinement_run.as_dict())

    extraction = await evidence.extract_performances(
        providers=evaluated,
        indicators=weighted_indicators,
        methodology=methodology,
        session_id=session_id,
        guardrail_log=guardrail_log,
        query_hints=query_hints,
    )
    llm_runs.extend(extraction.llm_runs)
    rag_audit.extend(extraction.rag_audit)
    evidences: Dict[str, list] = extraction.evidences

    # 7) Conjunto comparável (§11.1), renormalização (§11.2) e agregação (§12).
    #    Um indicador só entra se houver valor comparável para TODOS os
    #    provedores da avaliação; os que ficam de fora saem com o motivo, e os
    #    pesos dos que permanecem são renormalizados para somar 1.
    provider_ids = [p["id"] for p in evaluated]
    comparability = build_comparability_set(
        extraction.performances(),
        provider_ids,
        weight_set.global_weights().keys(),
        methodology,
    )
    effective_weights = renormalize_weights(
        weight_set.global_weights(), comparability.valid
    )
    scoring = compute_scores(evaluated, comparability, effective_weights, methodology)

    # 7.1) Sensibilidade: quanto o peso de uma dimensão precisaria mudar para o
    #      primeiro colocado deixar de ser o primeiro. Não altera o ranking —
    #      mede o quanto ele decorre das prioridades declaradas. Sem isso, uma
    #      liderança de 0,002 é exibida com a mesma firmeza de uma de 0,20.
    sensitivity = analyze_sensitivity(
        ranking=[{"id": s.provider_id, "score": s.score} for s in scoring.scores],
        dimension_weights=criteria_weights,
        local_weights={
            w.indicator_id: w.local_weight
            for w in weight_set.weights
            if w.local_weight is not None
        },
        dimension_by_indicator={i.id: i.dimension for i in methodology.indicators},
        normalized=comparability.normalized_by(),
        valid_indicators=comparability.valid,
        tie_break_tolerance=methodology.tie_break_tolerance,
    )

    # 8) Ranking, matriz para o dashboard e memória de cálculo da agregação.
    criteria_keys = list(criteria_weights.keys())
    ranking = [
        {
            "id": s.provider_id,
            "name": s.provider_name,
            "rank": s.rank,
            "score": round(s.score, 6),
            "tied": s.tied,
        }
        for s in scoring.scores
    ]
    synthesis = build_synthesis(
        scoring=scoring,
        extraction=extraction,
        comparability=comparability,
        criteria_weights=criteria_weights,
        criteria_keys=criteria_keys,
        methodology=methodology,
    )
    provider_scores = [
        {
            "id": s.provider_id,
            "name": s.provider_name,
            "rank": s.rank,
            "score": round(s.score, 6),
            **{
                c: round(v, 6)
                for c, v in dimension_performance(s).items()
            },
        }
        for s in scoring.scores
    ]

    # 9) Versões em vigor (§28): identificam com que questionário, algoritmo,
    #    prompts, modelos e configuração metodológica este resultado foi produzido.
    versions = {
        **audit.runtime_versions(prompt_versions=registered_versions()),
        **methodology.fingerprint(),
    }

    # Uma avaliação com guardrail recusado, LLM inválida ou questionário sem hash
    # não é uma avaliação limpa — e isso fica dito, não escondido.
    limitations: List[str] = []
    if versions.get("questions_hash") is None:
        limitations.append("Versão do questionário não pôde ser conferida (questions.json ilegível).")
    if llm_run.status != "OK":
        limitations.append("A justificativa textual não pôde ser gerada e validada.")
    extracoes_falhas = [
        run for run in extraction.llm_runs if run.get("status") != "OK"
    ]
    if extracoes_falhas:
        limitations.append(
            f"{len(extracoes_falhas)} extração(ões) de evidência não validada(s): os "
            "indicadores envolvidos ficaram sem evidência em vez de receber valor presumido."
        )
    # §11.1: indicador sem valor comparável em todos os provedores sai da conta.
    # A exclusão não penaliza ninguém, mas encolhe a base do ranking — e o gestor
    # precisa saber quanto do modelo efetivamente pesou no resultado.
    # Duas famílias de exclusão, e dizê-las na mesma frase seria falso: uma é
    # lacuna documental ("não se sabe"), a outra é resultado da avaliação ("os
    # três atendem igualmente"). A segunda não é limitação da base de evidência
    # e não pede nenhuma ação de quem lê — pede só que não se leia como diferença
    # o que o cálculo não separou.
    def _nomes(ids: Sequence[str]) -> str:
        nomes = ", ".join(methodology.by_id(i).name for i in list(ids)[:4])
        return f"{nomes}{'…' if len(ids) > 4 else ''}"

    sem_evidencia = [
        i
        for i, motivo in comparability.excluded.items()
        if motivo != EXCLUDED_NON_DISCRIMINATIVE
    ]
    equivalentes = [
        i
        for i, motivo in comparability.excluded.items()
        if motivo == EXCLUDED_NON_DISCRIMINATIVE
    ]
    if sem_evidencia:
        limitations.append(
            f"{len(sem_evidencia)} indicador(es) fora da comparação por falta de "
            f"evidência comparável em todos os provedores ({_nomes(sem_evidencia)})."
        )
    if equivalentes:
        limitations.append(
            f"{len(equivalentes)} indicador(es) deram a mesma nota a todos os provedores e "
            f"não entraram na soma, por não alterarem a ordem do ranking "
            f"({_nomes(equivalentes)}). A nota de cada um continua no relatório."
        )
    if not comparability.valid:
        limitations.append(
            "Nenhum indicador reuniu evidência comparável entre os provedores: o ranking "
            "não tem base documental e não deve ser usado como recomendação."
        )
    if excluded:
        limitations.append(
            f"{len(excluded)} provedor(es) fora da comparação por ausência de documentos indexados."
        )
    # §5.1: dimensão sem coeficiente válido pede revisão — o cálculo não inventa
    # pesos iguais para tapar o buraco, então a lacuna precisa ser dita.
    for dimension in weight_set.dimensions_needing_review:
        nome = methodology.dimension_name(dimension)
        limitations.append(
            f"Dimensão {nome}: nenhum indicador com relevância informada — os pesos "
            "locais não puderam ser calculados e a resposta precisa de revisão."
        )
    sem_peso = [w for w in weight_set.weights if w.global_weight is None]
    if sem_peso and not weight_set.dimensions_needing_review:
        limitations.append(
            f"{len(sem_peso)} indicador(es) sem peso por ausência de resposta de relevância."
        )
    status = STATUS_COMPLETED_WITH_LIMITATIONS if limitations else STATUS_COMPLETED

    # Os quatro níveis de peso de cada indicador, numa lista só: a resposta e o
    # registro de auditoria precisam ver exatamente os mesmos números, e montar
    # a lista duas vezes é como as duas visões se separam sem ninguém notar.
    indicator_weight_rows = [
        {
            **weight.as_dict(),
            "name": methodology.by_id(weight.indicator_id).name,
            # Peso depois da renormalização sobre o conjunto comparável (§11.2).
            # Difere do global sempre que algum indicador saiu por falta de
            # evidência — e é este que multiplicou o desempenho na pontuação.
            "effective_weight": scoring.effective_weights.get(weight.indicator_id),
            "is_valid_for_comparison": weight.indicator_id in comparability.valid,
            "excluded_reason": comparability.excluded.get(weight.indicator_id),
        }
        for weight in weight_set.weights
    ]

    # 10) Montar resposta
    response = {
        "ranking": ranking,
        "criteria_weights": criteria_weights,
        "provider_scores": provider_scores,
        "notes": notes,
        "evidences": evidences,
        # Memória de cálculo do AHP, para o relatório poder ser auditado
        "ahp": {
            **ahp_result,
            # Perfil de relevância dos indicadores (1-5) por dimensão: contexto do
            # relatório, fora do cálculo dos pesos (ver relevance_by_criterion).
            "relevance_by_criterion": relevance,
        },
        # Memória de cálculo da síntese: como cada score final foi obtido
        "synthesis": synthesis,
        # Robustez do 1º lugar: margem para o 2º e quanto o peso de cada
        # dimensão precisaria mudar para trocar o líder. É medida sobre o
        # resultado, não entrada dele.
        "sensitivity": sensitivity.as_dict() if sensitivity else None,
        # Pesos dos indicadores nos três níveis (§7), com a procedência de cada
        # coeficiente. É o que permite reconstruir por que um indicador pesa o
        # que pesa — se veio da dimensão priorizada ou da relevância declarada.
        "indicator_weights": {
            "indicators": indicator_weight_rows,
            "dimensions_needing_review": list(weight_set.dimensions_needing_review),
            "global_weight_sum": round(sum(weight_set.global_weights().values()), 6),
            "effective_weight_sum": round(sum(scoring.effective_weights.values()), 6),
            # O desempenho por indicador agora tem fonte: cada valor vem da
            # extração documental do RAG + LLM, validada em `evidence.py`.
            "performance_source": "evidence_extraction",
        },
        # Respostas fora do cálculo numérico (ver docstring de unscored_answers)
        "unscored_answers": q.unscored_answers(),
        # Cobertura documental: quem foi avaliado e quem ficou de fora, e por quê
        "coverage": {
            "evaluated": [
                {"id": p["id"], "name": p["name"], "chunks": chunk_counts.get(p["id"], 0)}
                for p in evaluated
            ],
            "excluded_no_documents": excluded,
            "scores_provenance": PROVIDER_SCORES_PROVENANCE,
            # Cobertura da extração (§29): quantas evidências foram encontradas,
            # quantas faltaram e quantas foram recusadas na validação. É a
            # medida de quanto do modelo o acervo documental sustentou.
            "evidence": {
                "by_status": extraction.coverage_by_status(),
                "indicators_requested": len(weighted_indicators),
                "indicators_in_comparison": len(comparability.valid),
                "excluded_indicators": dict(comparability.excluded),
                "comparability_rate": comparability.comparability_rate,
            },
        },
        "versions": versions,
        "status": status,
        "limitations": limitations,
        "guardrail_events": guardrail_log.as_dicts(),
    }

    # 11) Persistir para auditoria. Uma falha aqui não descarta o resultado que o
    #     gestor acabou de gerar — o relatório é devolvido com submission_id nulo,
    #     e a UI avisa que aquele envio não entrou no registro.
    try:
        response["submission_id"] = await run_in_threadpool(
            db.save_submission,
            q.audit_payload(),
            response,
            llm_runs,
            guardrail_log.as_dicts(),
            rag_audit,
            status,
            indicator_weight_rows,
        )
    except Exception:
        logger.exception("Falha ao gravar o envio no banco de auditoria")
        response["submission_id"] = None

    return response


# ========== Upload e ingestão RAG ==========


def _upload_dir_for_session(session_id: str) -> Path:
    """Diretório de upload da sessão: data/upload/<session_id>."""
    if not session_id or not session_id.strip():
        raise HTTPException(status_code=400, detail="session_id é obrigatório para upload.")
    d = documents.upload_base_dir() / session_id.strip()
    d.mkdir(parents=True, exist_ok=True)
    return d


@app.post("/api/documents/upload")
async def upload_documents(
    files: List[UploadFile] = File(...),
    session_id: Optional[str] = None,
):
    """
    Recebe um ou mais arquivos (PDF ou TXT) e session_id (query), salva em data/upload/<session_id>.
    Esses arquivos são usados somente na sessão ativa; ingerir via POST /api/documents/ingest?session_id=...

    Cada arquivo passa pelos guardrails da §23.3 **antes** de tocar o disco:
    extensão, tamanho, assinatura real do conteúdo e nome saneado. Extensão certa
    com conteúdo de executável não passa.
    """
    if not session_id:
        raise HTTPException(status_code=400, detail="Query 'session_id' é obrigatória para upload.")
    upload_dir = _upload_dir_for_session(session_id)
    if not files:
        raise HTTPException(status_code=400, detail="Nenhum arquivo enviado.")

    guardrail_log = GuardrailLog()
    try:
        enforce_document_quota(
            documents.count_documents(upload_dir), len(files), target=session_id, log=guardrail_log
        )
    except GuardrailRejection as exc:
        raise HTTPException(status_code=413, detail=exc.event.reason) from exc

    saved = []
    for f in files:
        content = await f.read()
        try:
            validated = validate_upload(f.filename or "", content, guardrail_log)
        except GuardrailRejection as exc:
            raise HTTPException(status_code=400, detail=exc.event.reason) from exc

        path = upload_dir / validated.stored_name
        path.write_bytes(content)
        saved.append(
            {
                "original_name": validated.original_name,
                "stored_name": validated.stored_name,
                "path": str(path),
                "size": validated.size,
                "detected_type": validated.detected_type,
            }
        )

    return {
        "uploaded": saved,
        "guardrail_events": guardrail_log.as_dicts(),
        "message": (
            f"{len(saved)} arquivo(s) salvo(s) para a sessão. "
            "Realize a ingestão da sessão para indexar no RAG."
        ),
    }


@app.get("/api/documents/file")
async def get_document_file(
    name: str,
    scope: str = SCOPE_GLOBAL,
    session_id: Optional[str] = None,
):
    """
    Serve um documento indexado para que a evidência do relatório possa linkar
    direto para o PDF de origem (ex.: .../file?name=x.pdf&scope=global#page=12).
    """
    if scope != SCOPE_GLOBAL:
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id é obrigatório para scope=session.")
        base_dir = documents.upload_base_dir() / session_id.strip()
    else:
        base_dir = documents.pdf_dir()

    # Aceita apenas o nome do arquivo e confirma que o caminho resolvido continua
    # dentro do diretório permitido — bloqueia travessia via "../" ou path absoluto.
    try:
        candidate = resolve_within(base_dir, name)
    except GuardrailRejection as exc:
        raise HTTPException(status_code=404, detail=exc.event.reason) from exc

    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Documento não encontrado.")
    if candidate.suffix.lower() not in documents.allowed_extensions():
        raise HTTPException(status_code=400, detail="Formato não permitido.")

    media_type = "application/pdf" if candidate.suffix.lower() == ".pdf" else "text/plain"
    # inline: o navegador abre no viewer (necessário para o #page=N funcionar)
    return FileResponse(
        candidate,
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{candidate.name}"'},
    )


@app.get("/api/documents/uploaded")
async def list_uploaded_documents(session_id: Optional[str] = None):
    """Lista arquivos em data/upload/<session_id>. session_id obrigatório."""
    if not session_id:
        return {"files": [], "message": "Informe session_id na query."}
    upload_dir = documents.upload_base_dir() / session_id.strip()
    if not upload_dir.is_dir():
        return {"files": []}
    files = []
    for p in upload_dir.iterdir():
        if p.is_file() and p.suffix.lower() in documents.allowed_extensions():
            files.append({"name": p.name, "size": p.stat().st_size})
    return {"files": files}


@app.post("/api/documents/ingest-global", dependencies=[Depends(auth.require_admin)])
async def run_ingest_global():
    """
    Ingestão dos documentos em data/pdf (incluídos pelo administrador).
    São indexados com scope=global e consultados em todas as buscas RAG.
    """
    paths = documents.global_paths()
    if not paths:
        return {
            "chunks": 0,
            "files_processed": 0,
            "message": "Nenhum arquivo em data/pdf. Coloque PDFs ou TXTs em data/pdf e chame novamente.",
            "details": [],
            "errors": [],
        }
    return await documents.run_ingestion(paths, SCOPE_GLOBAL, None)


@app.post("/api/documents/ingest")
async def run_ingest_session(session_id: Optional[str] = None):
    """
    Ingestão dos documentos da sessão em data/upload/<session_id>.

    São indexados com scope=evaluation:<session_id> e consultados apenas quando a
    sessão está ativa no recommend — o isolamento da §14.2, para que documento de
    uma avaliação não contamine a base global nem outra avaliação.
    """
    if not session_id:
        return {
            "chunks": 0,
            "files_processed": 0,
            "message": "Informe session_id na query para ingestão da sessão.",
            "details": [],
            "errors": [],
        }
    upload_dir = documents.upload_base_dir() / session_id.strip()
    if not upload_dir.is_dir():
        return {"chunks": 0, "files_processed": 0, "message": "Nenhum arquivo para esta sessão.", "details": [], "errors": []}
    paths = [str(p) for p in documents.list_files(upload_dir)]
    if not paths:
        return {"chunks": 0, "files_processed": 0, "message": "Nenhum arquivo no diretório da sessão.", "details": [], "errors": []}
    return await documents.run_ingestion(paths, evaluation_scope(session_id.strip()), session_id.strip())
