"""
Refinamento das consultas do RAG a partir do Bloco E (§4.5.1).

O gestor descreve requisitos institucionais em texto livre; a LLM os associa aos
indicadores já definidos; os termos resultantes entram **no fim** da consulta de
cada indicador. É o único efeito que o Bloco E tem no sistema — a §4.5.1 é
explícita em que essas informações "não alteram os pesos das dimensões calculados
pelo AHP nem os pesos locais dos indicadores".

A garantia não é uma verificação, é a forma do dado: esta função devolve
`Dict[indicator_id, Tuple[str, ...]]`, e o único consumidor é
`rag.query_for_indicator`. Não existe caminho por onde um termo alcance peso,
normalização ou pontuação.

Três limites deliberados sobre o que sai daqui:

  - **indicador desconhecido é descartado**, não criado. A §4.4 determina que os
    critérios sejam previamente definidos; um `indicator_id` inventado pela LLM é
    exatamente o que ela não pode fazer.

  - **poucos termos, curtos.** Refinar é ajustar o foco de uma consulta que já
    existe. Um "termo" de duzentos caracteres não é termo — é texto do gestor
    reaparecendo dentro do vetor da consulta, com peso desproporcional.

  - **falha não bloqueia.** Sem refinamento a avaliação roda com as consultas da
    pesquisa, que são a base metodológica. Recusar a avaliação porque um prompt
    auxiliar falhou trocaria um resultado bom por nenhum resultado.
"""

import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from domain.methodology import IndicatorConfig
from guardrails import GuardrailLog, format_qa_pairs, wrap_user_context
from guardrails.events import ACTION_REJECT, ACTION_WARN, STAGE_LLM_OUTPUT
from llm import get_llm_client
from llm.client import LLMRunRecord
from llm.prompts import get as get_prompt
from llm.schemas import QueryRefinement

logger = logging.getLogger("uvicorn.error")

PROMPT_ID = "PROMPT_QUERY_REFINEMENT_V1"

# Tetos do que a LLM pode acrescentar a uma consulta.
MAX_TERMS_PER_INDICATOR = 4
MAX_TERM_CHARS = 60


def describe_indicators_for_refinement(indicators: Sequence[IndicatorConfig]) -> str:
    """
    Lista compacta de indicadores para o prompt auxiliar.

    Só id, nome e dimensão: este prompt não avalia evidência, então rubricas,
    unidades e direção não têm o que informar aqui — e cada linha a mais é
    contexto competindo com o texto do gestor, que é o que interessa ler.
    """
    return "\n".join(
        f'- indicator_id: "{i.id}" · {i.name} ({i.dimension})' for i in indicators
    )


def _clean_terms(raw_terms: Sequence[Any], log: GuardrailLog, target: str) -> Tuple[str, ...]:
    """Normaliza, corta e deduplica os termos de um indicador."""
    limpos: List[str] = []
    vistos: set = set()
    for raw in raw_terms:
        termo = " ".join(str(raw or "").split())
        if not termo:
            continue
        if len(termo) > MAX_TERM_CHARS:
            log.record(
                rule_id="QUERY_REFINEMENT_TERM_TOO_LONG",
                stage=STAGE_LLM_OUTPUT,
                action=ACTION_REJECT,
                reason=(
                    f"Termo de busca com {len(termo)} caracteres (limite {MAX_TERM_CHARS}): "
                    "descartado para não dominar o vetor da consulta."
                ),
                target=target,
                masked_sample=termo[:40] + "…",
            )
            continue
        chave = termo.casefold()
        if chave in vistos:
            continue
        vistos.add(chave)
        limpos.append(termo)
        if len(limpos) >= MAX_TERMS_PER_INDICATOR:
            break
    return tuple(limpos)


async def refine_queries(
    qa_pairs: Sequence[Mapping[str, str]],
    indicators: Sequence[IndicatorConfig],
    guardrail_log: Optional[GuardrailLog] = None,
) -> Tuple[Dict[str, Tuple[str, ...]], Optional[LLMRunRecord]]:
    """
    Traduz os requisitos institucionais em termos de busca por indicador.

    `qa_pairs` são as respostas dissertativas **já saneadas** pelos guardrails de
    entrada — este módulo não repete a varredura de credenciais nem a heurística
    de injeção, que rodam uma vez em `preferences.sanitize_qa_pairs` e valem para
    o mesmo texto.

    Devolve `({}, None)` quando não há texto livre: sem requisito descrito não há
    o que refinar, e chamar a LLM ali só produziria termos inventados.
    """
    log = guardrail_log if guardrail_log is not None else GuardrailLog()

    com_texto = [p for p in qa_pairs if str(p.get("resposta", "")).strip()]
    if not com_texto or not indicators:
        return {}, None

    prompt = get_prompt(PROMPT_ID).render(
        indicators=describe_indicators_for_refinement(indicators),
        qa_pairs=wrap_user_context(format_qa_pairs(com_texto)),
    )

    # Mesmo cache da extração, pelo mesmo motivo — e aqui o efeito é maior: o
    # refinamento decide QUAIS trechos são recuperados, então uma variação aqui
    # muda o contexto entregue à extração e derruba o cache dela em cascata.
    # Estabilizar este passo é o que torna a avaliação inteira reproduzível.
    cached = _cache_lookup(prompt)
    if cached is not None:
        return _refinements_from_raw(cached, indicators, log), None

    result = await get_llm_client().structured_generate(prompt, QueryRefinement)

    if not result.ok or not isinstance(result.data, QueryRefinement):
        log.record(
            rule_id="QUERY_REFINEMENT_UNAVAILABLE",
            stage=STAGE_LLM_OUTPUT,
            action=ACTION_WARN,
            reason=(
                f"Refinamento das consultas não validado ({result.run.status}): as buscas "
                "seguem com os termos da pesquisa, sem o contexto institucional."
            ),
            target="bloco_e",
        )
        return {}, result.run

    _cache_store(prompt, result.data)
    return _refinements_from_raw(result.data, indicators, log), result.run


# ---------------------------------------------------------------------------
# Cache do refinamento
# ---------------------------------------------------------------------------
#
# Mesma ideia do cache de extração: guarda a saída BRUTA e refaz a limpeza dos
# termos (`_clean_terms`) e a checagem de indicador em cima dela, para que uma
# correção nessas regras valha também para os refinamentos já guardados.
#
# A chave é o prompt renderizado inteiro — ele já contém as respostas
# dissertativas do gestor e a lista de indicadores, que é tudo o que faz a
# resposta mudar. Texto diferente, chave diferente.


def _cache_coordenadas(prompt: Any) -> Optional[Tuple[str, str]]:
    try:
        import db
        from config import get_settings

        if not get_settings().llm_cache_enabled:
            return None
        modelo = str(get_settings().llm_model)
        impressao = db.chunks_fingerprint([prompt.system, prompt.user])
        chave = db.extraction_cache_key(
            "-", "query_refinement", impressao, prompt.prompt_id, prompt.prompt_version, modelo
        )
        return chave, impressao
    except Exception as exc:  # noqa: BLE001 - cache indisponível não é erro de avaliação
        logger.warning("Cache de refinamento indisponível: %s: %s", type(exc).__name__, exc)
        return None


def _cache_lookup(prompt: Any) -> Optional[QueryRefinement]:
    coord = _cache_coordenadas(prompt)
    if coord is None:
        return None
    try:
        import db

        bruto = db.get_cached_extraction(coord[0])
        return QueryRefinement.model_validate_json(bruto) if bruto else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Refinamento em cache descartado: %s: %s", type(exc).__name__, exc)
        return None


def _cache_store(prompt: Any, data: QueryRefinement) -> None:
    coord = _cache_coordenadas(prompt)
    if coord is None:
        return
    try:
        import db
        from config import get_settings

        db.save_cached_extraction(
            cache_key=coord[0],
            provider_id="-",
            dimension="query_refinement",
            chunks_hash=coord[1],
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.prompt_version,
            model=str(get_settings().llm_model),
            raw_response=data.model_dump_json(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Refinamento não guardado em cache: %s: %s", type(exc).__name__, exc)


def _refinements_from_raw(
    data: QueryRefinement,
    indicators: Sequence[IndicatorConfig],
    log: GuardrailLog,
) -> Dict[str, Tuple[str, ...]]:
    """Valida e limpa os termos. Roda igual no caminho novo e no do cache."""
    conhecidos = {i.id for i in indicators}
    refinamentos: Dict[str, Tuple[str, ...]] = {}

    for hint in data.refinements:
        if hint.indicator_id not in conhecidos:
            log.record(
                rule_id="QUERY_REFINEMENT_UNKNOWN_INDICATOR",
                stage=STAGE_LLM_OUTPUT,
                action=ACTION_REJECT,
                reason=f"Refinamento cita indicador fora da lista: {hint.indicator_id!r}.",
                target="bloco_e",
            )
            continue
        termos = _clean_terms(hint.terms, log, target=hint.indicator_id)
        if termos:
            # Duplicata do mesmo indicador: a primeira vale, sem escolher "a melhor".
            refinamentos.setdefault(hint.indicator_id, termos)

    return refinamentos


__all__ = [
    "MAX_TERMS_PER_INDICATOR",
    "MAX_TERM_CHARS",
    "PROMPT_ID",
    "describe_indicators_for_refinement",
    "refine_queries",
]
