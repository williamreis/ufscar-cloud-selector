import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from typing import Any, Dict, List, Optional

import audit
import auth
import db
import documents
import rag
from admin import router as admin_router
from ahp import compute_ahp_ranking, derive_criteria_weights
from config import get_settings
from domain import get_methodology, weights_from_answers
from guardrails import (
    GuardrailLog,
    GuardrailRejection,
    enforce_document_quota,
    resolve_within,
    validate_upload,
)
from llm.prompts import registered_versions
from preferences import explain_preferences
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
        "LLM=%s/%s · embeddings=%s/%s · algoritmo v%s",
        settings.llm_provider,
        settings.llm_model,
        settings.embedding_provider,
        settings.embedding_model,
        settings.scoring_algorithm_version,
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

    # 3b) Pesos dos indicadores (§5.1 e §7). A partir daqui os blocos A/B/C têm
    #     destino metodológico: relevância → coeficiente → peso local → peso
    #     global (peso da dimensão × peso local). Indicador sem coeficiente
    #     válido fica sem peso — nunca com zero, que afirmaria irrelevância.
    methodology = get_methodology()
    weight_set = weights_from_answers(q.answers_by_question(), criteria_weights, methodology)

    # 4) LLM redige a justificativa a partir das respostas dissertativas e dos
    #    pesos já calculados. Ele não altera nenhum número: o cálculo permanece
    #    determinístico e reprodutível a partir das respostas fechadas.
    #    O texto do gestor passa pelos guardrails de entrada antes de chegar ao
    #    prompt, e entra encapsulado como dado não confiável (§23.5 e §24).
    try:
        notes, llm_run = await explain_preferences(
            qa_pairs=q.qa_for_llm(),
            relevance=relevance,
            criteria_weights=criteria_weights,
            guardrail_log=guardrail_log,
        )
    except GuardrailRejection as exc:
        raise HTTPException(status_code=422, detail=exc.event.reason) from exc
    llm_runs.append(llm_run.as_dict())

    # 5) Só entram no ranking os provedores com base documental indexada.
    #    Sem documentos não há como sustentar a avaliação com evidência, então o
    #    provedor é excluído do relatório em vez de aparecer com nota sem lastro.
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

    # 6) Síntese das prioridades das alternativas, com a memória de cálculo
    #    célula a célula (nota → normalizada → contribuição → score).
    ranking, synthesis = compute_ahp_ranking(criteria_weights, evaluated)

    # 7) Matriz de scores por provedor (para dashboard: tabela e gráficos)
    providers_by_id = {p["id"]: p for p in evaluated}
    criteria_keys = list(criteria_weights.keys())
    provider_scores = []
    for _, row in ranking.iterrows():
        pid, name, total = row["id"], row["name"], float(row["score"])
        scores = providers_by_id.get(pid, {}).get("scores", {})
        provider_scores.append({
            "id": pid,
            "name": name,
            "rank": int(row["rank"]),
            "score": round(total, 4),
            **{c: round(float(scores.get(c, 0.5)), 4) for c in criteria_keys},
        })

    # 8) RAG para evidências, uma busca por (provedor × dimensão), de modo que cada
    #    trecho recuperado fique atrelado ao indicador que ele sustenta. Cada
    #    consulta e os trechos que ela devolveu ficam registrados (§27, bloco RAG).
    session_id = getattr(q, "session_id", None) or None
    evidences: Dict[str, list] = {}
    for provider in evaluated:
        pid, pname = provider["id"], provider["name"]
        items = []
        for criterion in criteria_keys:
            query_text = f"{pname}: {rag.query_for(criterion)}"
            hits = await run_in_threadpool(
                rag.search,
                query_text,
                # Restringe aos documentos deste provedor: sem isso, um provedor sem
                # documentos indexados receberia trechos de outro provedor como
                # "evidência" (a similaridade responde aos termos, não ao nome).
                2,
                session_id,
                pid,
            )
            rag_audit.append(
                {
                    "dimension": criterion,
                    "provider_id": pid,
                    "query_text": query_text,
                    "top_k": 2,
                    "chunks": hits,
                }
            )
            for h in hits:
                items.append({**h, "criterion": criterion})
        evidences[pid] = items

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
    if any(run["status"] != "OK" for run in llm_runs):
        limitations.append("A justificativa textual não pôde ser gerada e validada.")
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
    if not ahp_result.get("is_consistent", True):
        limitations.append(
            f"Razão de consistência do AHP acima do limite "
            f"({ahp_result['consistency_ratio']} > {ahp_result['consistency_threshold']}): "
            "os julgamentos par a par se contradizem e o ranking é preliminar."
        )
    status = STATUS_COMPLETED_WITH_LIMITATIONS if limitations else STATUS_COMPLETED

    # 10) Montar resposta
    response = {
        "ranking": ranking.to_dict(orient="records"),
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
        # Pesos dos indicadores nos três níveis (§7), com a procedência de cada
        # coeficiente. É o que permite reconstruir por que um indicador pesa o
        # que pesa — se veio da dimensão priorizada ou da relevância declarada.
        "indicator_weights": {
            "indicators": [
                {**weight.as_dict(), "name": methodology.by_id(weight.indicator_id).name}
                for weight in weight_set.weights
            ],
            "dimensions_needing_review": list(weight_set.dimensions_needing_review),
            "global_weight_sum": round(sum(weight_set.global_weights().values()), 6),
            # O motor de desempenho/agregação por indicador está implementado e
            # testado, mas ainda não tem fonte: a extração de evidência entra na
            # Fase 2. Até lá o ranking continua vindo da síntese por dimensão, e
            # nenhum valor por indicador é inventado para preencher a lacuna.
            "performance_source": "pending_evidence_extraction",
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
            weight_set.as_dicts(),
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
