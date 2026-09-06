"""
Extração das evidências documentais por indicador (§4.4.1 da dissertação).

É o elo que faltava entre o RAG e o motor determinístico. O caminho é:

    indicador → consulta RAG → trechos → LLM (Quadro 26) → validação →
    PerformanceInput → normalização (§9) → agregação (§12)

Este módulo cobre do primeiro ao penúltimo passo. Ele **não** normaliza, não
pondera e não ordena: devolve `PerformanceInput`, que é a entrada de
`domain/normalization.py`. A separação é a mesma que a §5.4 descreve — o
componente probabilístico identifica e interpreta a evidência, o determinístico
converte em valor comparável.

Três regras que este arquivo existe para fazer valer:

  - **a LLM não pontua.** Ela devolve valor publicado ou categoria de rubrica.
    O número que entra na matriz de desempenho vem da rubrica (`value_from_category`)
    ou é o próprio valor do documento; não há caminho em que uma nota inventada
    pelo modelo vire desempenho.

  - **evidência sem fonte verificável não vale (§19).** Toda `source_chunk_id`
    precisa estar entre os `chunk_id` que foram efetivamente entregues ao
    modelo. Um identificador inventado torna a evidência `INVALID`, não uma nota
    aproximada.

  - **unidade divergente não é comparável (§4.4.1.1).** Se um provedor publica
    "90 %" e outro "0,9 ratio" no mesmo indicador, os números não vão para a
    mesma régua: o indicador inteiro sai da comparação, para todas as
    alternativas. Comparar 90 com 0,9 produziria um ranking com aparência de
    normalidade e nenhum sentido.
"""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from starlette.concurrency import run_in_threadpool

import rag
from config import get_settings
from domain.methodology import IndicatorConfig, Methodology
from domain.normalization import (
    STATUS_FOUND,
    STATUS_INVALID,
    STATUS_NOT_FOUND,
    STATUS_PARTIAL,
    PerformanceInput,
    value_from_category,
)
from guardrails import GuardrailLog, chunk_label, wrap_document_context
from guardrails.events import ACTION_REJECT, ACTION_WARN, STAGE_LLM_OUTPUT
from llm import get_llm_client
from llm.client import LLMRunRecord
from llm.prompts import get as get_prompt
from llm.schemas import DimensionEvidence, IndicatorEvidence

logger = logging.getLogger("uvicorn.error")

PROMPT_ID = "PROMPT_EVIDENCE_EXTRACTION_V1"

# Trechos recuperados por indicador. Baixo de propósito: o contexto de uma
# chamada é a soma dos trechos de 4 a 5 indicadores, e um `top_k` generoso por
# indicador estoura o limite de tokens sem melhorar a extração.
CHUNKS_PER_INDICATOR = 3

# Teto de trechos numa única chamada, depois da deduplicação.
MAX_CHUNKS_PER_CALL = 14

# Chamadas simultâneas à LLM. O produto faz (provedores × dimensões) chamadas —
# com 3 provedores são 9 —, e dispará-las todas de uma vez bate no rate limit da
# maioria dos provedores comerciais. Em camada gratuita com teto de tokens por
# minuto (o Groq grátis dá 8 mil TPM, e uma chamada destas pede ~4,5 mil), mesmo
# duas em paralelo já estouram: daí `LLM_CONCURRENCY=1` no .env. O valor sai de
# configuração; a constante permanece como default de quem chama sem settings.
DEFAULT_CONCURRENCY = 3


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """
    Evidência de um indicador para um provedor, já validada.

    `status` segue os estados da §11 (`FOUND`, `PARTIAL`, `NOT_FOUND`,
    `INVALID`). `rejection` diz por que uma resposta da LLM foi rebaixada — é o
    que permite distinguir, no relatório, "o documento não tem" de "o modelo
    respondeu fora das regras".
    """

    provider_id: str
    indicator_id: str
    dimension: str
    status: str
    nature: Optional[str] = None
    # "o valor ou característica extraída" da §5.4, como o documento a apresenta.
    extracted_value: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    category: Optional[str] = None
    summary: Optional[str] = None
    source_chunk_id: Optional[str] = None
    source_document: Optional[str] = None
    rejection: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "indicator_id": self.indicator_id,
            "dimension": self.dimension,
            "status": self.status,
            "nature": self.nature,
            "extracted_value": self.extracted_value,
            "value": self.value,
            "unit": self.unit,
            "category": self.category,
            "summary": self.summary,
            "source_chunk_id": self.source_chunk_id,
            "source_document": self.source_document,
            "rejection": self.rejection,
        }

    def to_performance(self) -> PerformanceInput:
        return PerformanceInput(
            provider_id=self.provider_id,
            indicator_id=self.indicator_id,
            status=self.status,
            value=self.value,
            raw_value=self.extracted_value or self.summary,
            unit=self.unit,
            qualitative_category=self.category,
        )


@dataclass
class ExtractionResult:
    """Tudo o que a extração produziu, separado por destino."""

    findings: Tuple[Finding, ...] = ()
    llm_runs: List[Dict[str, Any]] = field(default_factory=list)
    rag_audit: List[Dict[str, Any]] = field(default_factory=list)
    evidences: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

    def performances(self) -> List[PerformanceInput]:
        return [f.to_performance() for f in self.findings]

    def coverage_by_status(self) -> Dict[str, int]:
        contagem: Dict[str, int] = {}
        for f in self.findings:
            contagem[f.status] = contagem.get(f.status, 0) + 1
        return contagem


# ---------------------------------------------------------------------------
# Unidades
# ---------------------------------------------------------------------------

_UNIT_ALIASES = {
    "%": "%",
    "percent": "%",
    "percentage": "%",
    "porcento": "%",
    "porcentagem": "%",
    "pontos percentuais": "%",
    "ms": "ms",
    "milissegundos": "ms",
    "milliseconds": "ms",
    "millisecond": "ms",
    "s": "s",
    "segundos": "s",
    "seconds": "s",
    "ratio": "ratio",
    "razao": "ratio",
    "razão": "ratio",
    "adimensional": "ratio",
    "pue": "ratio",
    "dcie": "ratio",
}


def canonical_unit(unit: Optional[str]) -> Optional[str]:
    """
    Reduz a unidade a uma forma canônica para comparação.

    Não converte nada — só reconhece que "percent" e "%" são a mesma coisa. A
    conversão de unidade é justamente o que a regra 4 do prompt proíbe a LLM de
    fazer, e fazê-la aqui em silêncio seria a mesma decisão, escondida um nível
    abaixo.
    """
    if unit is None:
        return None
    limpo = re.sub(r"[\s._]+", " ", str(unit)).strip().lower()
    if not limpo:
        return None
    return _UNIT_ALIASES.get(limpo, limpo)


# Número precedido, no máximo, de um operador de comparação e seguido, no
# máximo, de um símbolo de unidade. Nada além disso: "1-minute intervals" e
# "entre 10 e 50 ms" não casam, e devem mesmo continuar fora.
_NUMERO_COM_COMPARADOR = re.compile(
    r"^\s*(?:[<>]=?|[≤≥]|no\s+m[íi]nimo|pelo\s+menos|at\s+least|up\s+to|about|~)?\s*"
    r"(\d+(?:[.,]\d+)?)\s*"
    r"(?:%|percent|ms|milliseconds?|pue|ratio)?\s*$",
    re.IGNORECASE,
)


def numero_do_texto(texto: Optional[str]) -> Optional[float]:
    """
    Lê o número de um `extracted_value` que veio com operador de comparação.

    O caso concreto: o SLA do Google Cloud publica "≥ 99.99%", e o modelo
    devolvia `value: null` com o texto integral em `extracted_value`. A validação
    recusava por "indicador quantitativo sem valor numérico" — descartando um
    SLA que estava perfeitamente legível, e derrubando `performance_availability`
    para todos os provedores por falta de par comparável.

    Isto **não é conversão nem inferência**: o número devolvido é o mesmo que
    está escrito no texto que o próprio modelo extraiu, sem mudar grandeza,
    unidade ou escala. O que a função remove é o operador — que é formato, não
    valor. Faixas, intervalos e texto sem número não casam com o padrão e
    continuam fora, porque ali não há um valor único a comparar.
    """
    if not texto:
        return None
    casado = _NUMERO_COM_COMPARADOR.match(str(texto))
    if casado is None:
        return None
    try:
        return float(casado.group(1).replace(",", "."))
    except ValueError:
        return None


def _unit_is_expected(unit: Optional[str], indicator: IndicatorConfig) -> bool:
    """
    A unidade informada está entre as que o indicador admite?

    A comparação é sobre a forma canônica dos dois lados, para que "percent" e "%"
    contem como a mesma unidade — reconhecer sinônimo não é converter grandeza.

    Indicador sem `expected_units` aceita qualquer unidade: a ausência da lista é
    "não foi especificado", não "nada é aceito".
    """
    esperadas = {canonical_unit(u) for u in indicator.expected_units}
    esperadas.discard(None)
    if not esperadas:
        return True
    return canonical_unit(unit) in esperadas


# ---------------------------------------------------------------------------
# Recuperação
# ---------------------------------------------------------------------------


def _retrieve_for_indicator(
    indicator: IndicatorConfig,
    provider: Mapping[str, str],
    session_id: Optional[str],
    extra_terms: Sequence[str] = (),
) -> Tuple[str, List[Dict[str, Any]]]:
    """Consulta o índice para um par (provedor, indicador). Bloqueante."""
    query_text = rag.query_for_indicator(indicator, provider.get("name"), extra_terms)
    hits = rag.search(query_text, CHUNKS_PER_INDICATOR, session_id, provider["id"])
    return query_text, hits


def _dedupe_chunks(chunks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove trechos repetidos, preservando a ordem de chegada.

    Indicadores da mesma dimensão recuperam o mesmo trecho com frequência (um
    relatório ambiental fala de PUE e de energia renovável no mesmo parágrafo).
    Enviar o trecho duas vezes só gasta contexto.
    """
    vistos: set = set()
    saida: List[Dict[str, Any]] = []
    for chunk in chunks:
        chave = chunk.get("chunk_id") or chunk.get("content_hash") or chunk.get("page_content")
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append(chunk)
    return saida


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


def describe_indicators(indicators: Sequence[IndicatorConfig]) -> str:
    """
    Lista de indicadores para o prompt, com o que cada um admite como resposta.

    Três coisas acompanham cada indicador, e cada uma existe por uma exigência
    diferente do texto:

      - **a allowlist da rubrica**, nos qualitativos. Sem ela o modelo não teria
        como escolher uma categoria válida, e a regra 5 do prompt seria
        impossível de cumprir em vez de apenas obrigatória;

      - **as unidades esperadas**, nos quantitativos, para que a regra 4 tenha
        referência do que "a unidade correspondente" significa naquele indicador;

      - **os termos do Quadro 27**, que a §5.2 descreve como "elementos
        orientadores na construção das consultas e na recuperação das evidências
        documentais". Eles já orientam a consulta ao índice; aqui orientam
        também a leitura do trecho, que é a outra metade da mesma frase.
    """
    linhas: List[str] = []
    for indicator in indicators:
        partes = [f'- indicator_id: "{indicator.id}"', f"  nome: {indicator.name}"]
        if indicator.is_quantitative:
            partes.append("  tipo: quantitativo — preencha `value` e `unit`, deixe `category` nulo")
            if indicator.expected_units:
                partes.append(f"  unidades esperadas: {', '.join(indicator.expected_units)}")
        else:
            partes.append("  tipo: qualitativo — preencha `category`, deixe `value` e `unit` nulos")
            partes.append("  categorias permitidas (escolha pela condição da evidência):")
            rubrica = indicator.rubric
            for categoria in rubrica.allowed_categories if rubrica else ():
                condicao = rubrica.condition_for(categoria) if rubrica else None
                partes.append(
                    f"    - {categoria}: {condicao}" if condicao else f"    - {categoria}"
                )
        # Desambiguação de grandeza, quando o indicador tem uma. Vem antes dos
        # termos de propósito: os termos dizem onde procurar, e casam com mais de
        # uma grandeza no mesmo parágrafo; a dica diz qual delas é o indicador.
        if indicator.evidence_hint:
            partes.append(f"  atenção: {indicator.evidence_hint}")
        # Termos do Quadro 27, inteiros: a §5.2 os apresenta como orientadores
        # "na construção das consultas e na recuperação das evidências", e o
        # recorte que existia aqui não vinha da diretriz.
        termos = [t for t in indicator.search_terms if t and t.strip()]
        if termos:
            partes.append(f"  termos relacionados: {', '.join(termos)}")
        linhas.append("\n".join(partes))
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Validação da saída
# ---------------------------------------------------------------------------


def _parcial(
    raw: IndicatorEvidence,
    indicator: IndicatorConfig,
    provider_id: str,
    partial_status: str,
    source_chunk_id: Optional[str] = None,
) -> Finding:
    """
    Evidência declarada parcial: o trecho trata do tema sem trazer o valor.

    Fica sem `value` e sem `category` de propósito — é o que a distingue de uma
    evidência completa e o que a mantém fora do cálculo. A fonte é preservada
    para que o relatório possa mostrar em que trecho o modelo se apoiou.
    """
    return Finding(
        provider_id=provider_id,
        indicator_id=indicator.id,
        dimension=indicator.dimension,
        status=partial_status,
        nature=raw.nature,
        extracted_value=raw.extracted_value,
        summary=raw.summary,
        source_chunk_id=source_chunk_id or raw.source_chunk_id,
        source_document=raw.source_document,
    )


def _validate_finding(
    raw: IndicatorEvidence,
    indicator: IndicatorConfig,
    provider_id: str,
    fontes_entregues: Mapping[str, str],
    methodology: Methodology,
    log: GuardrailLog,
) -> Finding:
    """
    Converte uma entrada da LLM em `Finding`, aplicando as regras da §19.

    Qualquer desvio vira `INVALID` com motivo registrado — nunca um valor
    aproximado. `INVALID` e `NOT_FOUND` levam ao mesmo lugar no cálculo (o
    indicador sai da comparação), mas dizem coisas diferentes ao gestor, e o
    relatório precisa poder distingui-las.

    O estado `PARTIAL` obedece a `partial_counts_as_comparable` do `scales.json`
    (§29.2): com `false` — o padrão — ele fica fora do conjunto comparável, por
    decisão acadêmica ainda em aberto, não por limitação do código.

    Desde a versão 4 do prompt, `PARTIAL` chega **sem valor e sem categoria**: a
    regra 12 o reserva para meta futura, recorte parcial ou tema sem número, e
    manda os campos virem nulos. Uma resposta com valor e estado `PARTIAL` deixou
    de ser esperada; se vier, o valor é preservado e o estado é respeitado — o
    código não promove a evidência por conta própria, porque a distinção entre
    "100% de energia renovável em 2023" e "compromisso de 100% até 2025" está no
    texto, não no número, e só quem leu o trecho pode fazê-la.
    """
    partial_status = (
        STATUS_FOUND if methodology.partial_counts_as_comparable else STATUS_PARTIAL
    )

    def rejeitar(rule_id: str, motivo: str) -> Finding:
        log.record(
            rule_id=rule_id,
            stage=STAGE_LLM_OUTPUT,
            action=ACTION_REJECT,
            reason=motivo,
            target=f"{provider_id}/{indicator.id}",
        )
        return Finding(
            provider_id=provider_id,
            indicator_id=indicator.id,
            dimension=indicator.dimension,
            status=STATUS_INVALID,
            nature=raw.nature,
            extracted_value=raw.extracted_value,
            summary=raw.summary,
            rejection=motivo,
        )

    # Ausência declarada: nada a validar além do próprio estado.
    if raw.evidence_status == "NOT_FOUND":
        return Finding(
            provider_id=provider_id,
            indicator_id=indicator.id,
            dimension=indicator.dimension,
            status=STATUS_NOT_FOUND,
            nature=raw.nature,
            summary=raw.summary or "não identificado nas fontes recuperadas",
        )

    # §19: a evidência precisa apontar para um trecho que foi mesmo fornecido.
    # A verificação só é possível quando os trechos têm identificador — índices
    # gerados antes do `chunk_id` não têm, e aí a regra fica sem base para valer.
    #
    # `fontes_entregues` aceita o rótulo curto do bloco (`T1`, `T2`…) e também o
    # `chunk_id` real: o rótulo é o que o prompt pede, e o `chunk_id` continua
    # valendo para não invalidar respostas de execuções anteriores. Os dois
    # resolvem para o mesmo `chunk_id`, que é o que fica gravado — o rótulo é
    # endereço dentro de uma chamada, não identidade do trecho.
    chunk_id = None
    if fontes_entregues:
        citado_bruto = str(raw.source_chunk_id or "").strip()
        chunk_id = fontes_entregues.get(citado_bruto) or fontes_entregues.get(
            citado_bruto.upper()
        )
        if chunk_id is None:
            citado = raw.source_chunk_id or "nenhum"
            return rejeitar(
                "EVIDENCE_SOURCE_NOT_PROVIDED",
                f"Evidência cita trecho fora do contexto entregue (id={citado}).",
            )

    if indicator.is_quantitative:
        valor_bruto = raw.value
        if valor_bruto is None:
            # Antes de recusar: o número pode estar em `extracted_value` com um
            # operador de comparação na frente ("≥ 99.99%"). Ler ali é recuperar
            # o que o modelo já extraiu, não inferir — e fica registrado.
            recuperado = numero_do_texto(raw.extracted_value)
            if recuperado is not None:
                log.record(
                    rule_id="EVIDENCE_VALUE_RECOVERED_FROM_TEXT",
                    stage=STAGE_LLM_OUTPUT,
                    action=ACTION_WARN,
                    reason=(
                        f"Valor lido de extracted_value {raw.extracted_value!r} "
                        f"por vir com operador de comparação: {recuperado}."
                    ),
                    target=f"{provider_id}/{indicator.id}",
                )
                valor_bruto = recuperado

        if valor_bruto is None:
            # `PARTIAL` sem valor é a resposta certa, não uma resposta fora das
            # regras: a regra 12 do prompt manda usá-lo justamente quando o
            # trecho traz meta futura, recorte parcial ou o tema sem número — e
            # manda deixar `value` nulo nesse caso. Tratar isso como INVALID
            # acusaria o modelo de violar a regra que ele acabou de cumprir.
            if raw.evidence_status == "PARTIAL":
                return _parcial(raw, indicator, provider_id, partial_status, chunk_id)
            return rejeitar(
                "EVIDENCE_MISSING_VALUE",
                "Indicador quantitativo sem valor numérico na resposta.",
            )

        # §5.4: "a validação das estruturas retornadas busca evitar que informações
        # fora do formato esperado sejam incorporadas diretamente ao processo de
        # avaliação". A unidade é parte do formato — um valor na unidade errada é
        # numericamente válido e metodologicamente outro indicador.
        #
        # O caso que motiva a regra: o Quadro 22 define o CUE como razão (emissão
        # de gases de efeito estufa ÷ energia dos equipamentos). Um total absoluto
        # em tCO2e passa pela normalização sem erro e faz o provedor maior perder
        # por ser maior, não por ser menos eficiente.
        if indicator.expected_units and not _unit_is_expected(raw.unit, indicator):
            informada = raw.unit or "nenhuma"
            return rejeitar(
                "EVIDENCE_UNEXPECTED_UNIT",
                f"Unidade {informada!r} fora das esperadas para o indicador "
                f"({', '.join(indicator.expected_units)}).",
            )
        status = STATUS_FOUND if raw.evidence_status == "FOUND" else partial_status
        return Finding(
            provider_id=provider_id,
            indicator_id=indicator.id,
            dimension=indicator.dimension,
            status=status,
            nature=raw.nature,
            extracted_value=raw.extracted_value,
            value=float(valor_bruto),
            unit=raw.unit,
            summary=raw.summary,
            source_chunk_id=chunk_id or raw.source_chunk_id,
            source_document=raw.source_document,
        )

    # Qualitativo: quem converte categoria em número é a rubrica (§10.1).
    valor, status_rubrica = value_from_category(indicator, raw.category)
    if status_rubrica == STATUS_INVALID:
        permitidas = ", ".join(indicator.rubric.allowed_categories) if indicator.rubric else "—"
        return rejeitar(
            "EVIDENCE_CATEGORY_NOT_ALLOWED",
            f"Categoria {raw.category!r} fora da rubrica do indicador (permitidas: {permitidas}).",
        )
    if status_rubrica == STATUS_NOT_FOUND:
        if raw.evidence_status == "PARTIAL":
            return _parcial(raw, indicator, provider_id, partial_status, chunk_id)
        return rejeitar(
            "EVIDENCE_MISSING_CATEGORY",
            "Indicador qualitativo sem categoria na resposta.",
        )

    status = STATUS_FOUND if raw.evidence_status == "FOUND" else partial_status
    return Finding(
        provider_id=provider_id,
        indicator_id=indicator.id,
        dimension=indicator.dimension,
        status=status,
        nature=raw.nature,
        extracted_value=raw.extracted_value,
        value=valor,
        category=raw.category,
        summary=raw.summary,
        source_chunk_id=chunk_id or raw.source_chunk_id,
        source_document=raw.source_document,
    )


def _missing(provider_id: str, indicator: IndicatorConfig, motivo: str) -> Finding:
    """Indicador que a LLM não respondeu, ou que nem chegou a ser consultado."""
    return Finding(
        provider_id=provider_id,
        indicator_id=indicator.id,
        dimension=indicator.dimension,
        status=STATUS_NOT_FOUND,
        nature="insufficient",
        summary="não identificado nas fontes recuperadas",
        rejection=motivo,
    )


def enforce_unit_consistency(
    findings: Sequence[Finding],
    methodology: Methodology,
    log: GuardrailLog,
) -> Tuple[Finding, ...]:
    """
    Invalida indicadores quantitativos cujos provedores publicam unidades
    diferentes (§4.4.1.1: os valores precisam ser "mensuráveis e comparáveis").

    A invalidação é do indicador inteiro, em todas as alternativas — a mesma
    lógica da §11.1. Invalidar só o provedor divergente deixaria os demais
    disputando uma régua da qual ele foi retirado por um motivo que não é
    desempenho.
    """
    por_indicador: Dict[str, List[Finding]] = {}
    for finding in findings:
        if finding.status in (STATUS_FOUND, STATUS_PARTIAL):
            por_indicador.setdefault(finding.indicator_id, []).append(finding)

    inconsistentes: Dict[str, set] = {}
    for indicator_id, grupo in por_indicador.items():
        try:
            indicator = methodology.by_id(indicator_id)
        except KeyError:
            continue
        if not indicator.is_quantitative:
            continue
        unidades = {canonical_unit(f.unit) for f in grupo}
        unidades.discard(None)
        if len(unidades) > 1:
            inconsistentes[indicator_id] = unidades

    if not inconsistentes:
        return tuple(findings)

    for indicator_id, unidades in inconsistentes.items():
        log.record(
            rule_id="EVIDENCE_UNIT_MISMATCH",
            stage=STAGE_LLM_OUTPUT,
            action=ACTION_REJECT,
            reason=(
                f"Unidades divergentes entre provedores ({', '.join(sorted(unidades))}): "
                "o indicador não é comparável e sai da avaliação."
            ),
            target=indicator_id,
        )

    ajustados: List[Finding] = []
    for finding in findings:
        unidades = inconsistentes.get(finding.indicator_id)
        if unidades is None or finding.status not in (STATUS_FOUND, STATUS_PARTIAL):
            ajustados.append(finding)
            continue
        ajustados.append(
            Finding(
                provider_id=finding.provider_id,
                indicator_id=finding.indicator_id,
                dimension=finding.dimension,
                status=STATUS_INVALID,
                nature=finding.nature,
                extracted_value=finding.extracted_value,
                value=finding.value,
                unit=finding.unit,
                category=finding.category,
                summary=finding.summary,
                source_chunk_id=finding.source_chunk_id,
                source_document=finding.source_document,
                rejection=(
                    "Unidades divergentes entre provedores "
                    f"({', '.join(sorted(unidades))}) — indicador não comparável."
                ),
            )
        )
    return tuple(ajustados)


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Cache de extração
# ---------------------------------------------------------------------------
#
# Guarda a resposta BRUTA da LLM, antes da validação. A validação (§19), a
# rubrica (§10.1) e a normalização (§9) tornam a rodar sobre o texto guardado —
# uma correção de regra passa a valer para as leituras já em cache em vez de
# ficar congelada nelas.
#
# O cache é otimização, não dependência: qualquer falha ao consultá-lo ou
# gravá-lo é registrada e a extração segue pela LLM. Uma avaliação nunca deixa
# de acontecer porque o banco de auditoria não respondeu.


def _cache_coordenadas(
    provider_id: str,
    dimension: str,
    chunks: Sequence[Dict[str, Any]],
    prompt: Any,
    indicators_spec: str,
) -> Optional[Tuple[str, str, str, str]]:
    """
    `(chave, impressão dos trechos, modelo, impressão dos indicadores)` — ou
    `None` se não houver chave estável.

    `indicators_spec` é o bloco de indicadores como ele vai para a LLM. Entra na
    chave porque carrega as condições da rubrica, que vêm de `scales.json` e não
    do texto do prompt: refinar uma condição muda a resposta sem mudar
    `prompt_version`, e sem isto o cache devolveria a leitura feita sob a regra
    anterior como se nada tivesse mudado.
    """
    chunk_ids = [str(c["chunk_id"]) for c in chunks if c.get("chunk_id")]
    if not chunk_ids:
        # Índice antigo, sem `chunk_id`: não há como saber se os trechos são os
        # mesmos, e uma chave frouxa devolveria a leitura de outro contexto.
        return None
    try:
        import hashlib

        import db
        from config import get_settings

        if not get_settings().llm_cache_enabled:
            return None
        modelo = str(get_settings().llm_model)
        impressao = db.chunks_fingerprint(chunk_ids)
        impressao_indicadores = hashlib.sha256(indicators_spec.encode("utf-8")).hexdigest()
        chave = db.extraction_cache_key(
            provider_id,
            dimension,
            impressao,
            prompt.prompt_id,
            prompt.prompt_version,
            modelo,
            impressao_indicadores,
        )
        return chave, impressao, modelo, impressao_indicadores
    except Exception as exc:  # noqa: BLE001 - cache indisponível não é erro de avaliação
        logger.warning("Cache de extração indisponível: %s: %s", type(exc).__name__, exc)
        return None


def _cache_lookup(
    provider_id: str,
    dimension: str,
    chunks: Sequence[Dict[str, Any]],
    prompt: Any,
    indicators_spec: str,
) -> Optional["DimensionEvidence"]:
    coord = _cache_coordenadas(provider_id, dimension, chunks, prompt, indicators_spec)
    if coord is None:
        return None
    chave, _, _, _ = coord
    try:
        import db

        bruto = db.get_cached_extraction(chave)
        if bruto is None:
            return None
        return DimensionEvidence.model_validate_json(bruto)
    except Exception as exc:  # noqa: BLE001
        # Entrada corrompida ou esquema mudado: extrai de novo em vez de falhar.
        logger.warning(
            "Leitura em cache descartada para %s/%s: %s: %s",
            provider_id, dimension, type(exc).__name__, exc,
        )
        return None


def _cache_store(
    provider_id: str,
    dimension: str,
    chunks: Sequence[Dict[str, Any]],
    prompt: Any,
    data: "DimensionEvidence",
    indicators_spec: str,
) -> None:
    coord = _cache_coordenadas(provider_id, dimension, chunks, prompt, indicators_spec)
    if coord is None:
        return
    chave, impressao, modelo, impressao_indicadores = coord
    try:
        import db

        db.save_cached_extraction(
            cache_key=chave,
            provider_id=provider_id,
            dimension=dimension,
            chunks_hash=impressao,
            indicators_hash=impressao_indicadores,
            prompt_id=prompt.prompt_id,
            prompt_version=prompt.prompt_version,
            model=modelo,
            raw_response=data.model_dump_json(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Extração não guardada em cache: %s: %s", type(exc).__name__, exc)


def _findings_from_raw(
    data: "DimensionEvidence",
    indicators: Sequence[IndicatorConfig],
    provider_id: str,
    chunks: Sequence[Dict[str, Any]],
    methodology: Methodology,
    log: GuardrailLog,
) -> List[Finding]:
    """
    Valida a resposta bruta e devolve um `Finding` por indicador pedido.

    Separada de `_extract_dimension` porque roda nos dois caminhos — extração
    nova e leitura em cache —, e precisa ser exatamente a mesma nos dois: se o
    cache pulasse a validação, uma leitura guardada entraria no cálculo por uma
    porta que a §19 não vigia.
    """
    # Rótulo entregue no bloco (T1, T2…) → chunk_id real. O chunk_id também é
    # aceito como chave, para uma resposta que o traga continuar válida.
    fontes_entregues: Dict[str, str] = {}
    for indice, c in enumerate(chunks):
        real = str(c["chunk_id"]) if c.get("chunk_id") else None
        if real is None:
            continue
        fontes_entregues[chunk_label(indice)] = real
        fontes_entregues[real] = real

    dimension = indicators[0].dimension if indicators else ""
    por_id = {i.id: i for i in indicators}
    respondidos: Dict[str, Finding] = {}

    for raw in data.findings:
        indicator = por_id.get(raw.indicator_id)
        if indicator is None:
            # Regra 3 do prompt: indicador fora da lista fornecida.
            log.record(
                rule_id="EVIDENCE_UNKNOWN_INDICATOR",
                stage=STAGE_LLM_OUTPUT,
                action=ACTION_REJECT,
                reason=f"Resposta cita indicador fora da lista: {raw.indicator_id!r}.",
                target=f"{provider_id}/{dimension}",
            )
            continue
        if indicator.id in respondidos:
            continue  # duplicata: vale a primeira, sem escolher "a melhor"
        respondidos[indicator.id] = _validate_finding(
            raw, indicator, provider_id, fontes_entregues, methodology, log
        )

    findings: List[Finding] = []
    for indicator in indicators:
        finding = respondidos.get(indicator.id)
        if finding is None:
            log.record(
                rule_id="EVIDENCE_INDICATOR_OMITTED",
                stage=STAGE_LLM_OUTPUT,
                action=ACTION_WARN,
                reason="Indicador não veio na resposta da extração; registrado como sem evidência.",
                target=f"{provider_id}/{indicator.id}",
            )
            finding = _missing(provider_id, indicator, "Indicador omitido pela extração.")
        findings.append(finding)

    return findings


async def _extract_dimension(
    provider: Mapping[str, str],
    dimension: str,
    indicators: Sequence[IndicatorConfig],
    chunks: List[Dict[str, Any]],
    methodology: Methodology,
    log: GuardrailLog,
    semaphore: asyncio.Semaphore,
) -> Tuple[List[Finding], Optional[LLMRunRecord]]:
    """Uma chamada à LLM: um provedor, uma dimensão, os indicadores dela."""
    provider_id = provider["id"]

    if not chunks:
        # Sem trecho recuperado não há o que interpretar. Chamar a LLM aqui
        # convidaria exatamente a resposta que a regra 2 do prompt proíbe.
        return [_missing(provider_id, i, "Nenhum trecho recuperado para o indicador.") for i in indicators], None

    # Renderizado uma vez e reaproveitado: é o texto que vai à LLM e também o
    # que identifica a leitura no cache. Montá-lo duas vezes abriria espaço para
    # a chave descrever uma especificação diferente da que foi enviada.
    indicators_spec = describe_indicators(indicators)

    prompt = get_prompt(PROMPT_ID).render(
        provider_name=str(provider.get("name") or provider_id),
        dimension_name=methodology.dimension_name(dimension),
        indicators=indicators_spec,
        document_context=wrap_document_context(chunks, log),
    )

    # Leitura já feita para estes mesmos trechos, com este prompt e este modelo.
    # Guardar a extração é o que torna dois envios idênticos comparáveis: sem
    # isso, o não-determinismo residual do modelo (temperatura 0 não o elimina)
    # fazia o mesmo documento sair `FOUND` numa execução e `PARTIAL` na
    # seguinte, e a §11.1 derrubava o indicador inteiro por causa de uma célula.
    cached = _cache_lookup(provider_id, dimension, chunks, prompt, indicators_spec)
    if cached is not None:
        return _findings_from_raw(
            cached, indicators, provider_id, chunks, methodology, log
        ), None

    async with semaphore:
        result = await get_llm_client().structured_generate(prompt, DimensionEvidence)

    if not result.ok or not isinstance(result.data, DimensionEvidence):
        log.record(
            rule_id="EVIDENCE_EXTRACTION_UNAVAILABLE",
            stage=STAGE_LLM_OUTPUT,
            action=ACTION_WARN,
            reason=(
                f"Extração de evidências não validada ({result.run.status}): "
                f"{result.run.error or 'sem detalhe'}."
            ),
            target=f"{provider_id}/{dimension}",
        )
        motivo = f"Extração indisponível ({result.run.status})."
        return [_missing(provider_id, i, motivo) for i in indicators], result.run

    _cache_store(provider_id, dimension, chunks, prompt, result.data, indicators_spec)
    return _findings_from_raw(
        result.data, indicators, provider_id, chunks, methodology, log
    ), result.run


async def extract_performances(
    providers: Sequence[Mapping[str, str]],
    indicators: Sequence[IndicatorConfig],
    methodology: Methodology,
    session_id: Optional[str] = None,
    guardrail_log: Optional[GuardrailLog] = None,
    concurrency: Optional[int] = None,
    query_hints: Optional[Mapping[str, Sequence[str]]] = None,
) -> ExtractionResult:
    """
    Recupera e interpreta as evidências de todos os provedores.

    `indicators` são os que efetivamente receberam peso: indicador sem peso não
    é consultado, porque o gestor não deu base para ele entrar na comparação e
    consultá-lo só gastaria chamada.

    `query_hints` são os termos que o Bloco E acrescentou a cada consulta
    (§4.5.1). Entram no fim da consulta, depois dos termos da pesquisa, e não
    tocam em mais nada do pipeline.

    `concurrency` sem valor segue `LLM_CONCURRENCY` da configuração: quem paga
    por token tolera paralelismo, quem está na camada gratuita precisa de 1.
    """
    log = guardrail_log if guardrail_log is not None else GuardrailLog()
    resultado = ExtractionResult()

    if not providers or not indicators:
        return resultado

    por_dimensao: Dict[str, List[IndicatorConfig]] = {}
    for indicator in indicators:
        por_dimensao.setdefault(indicator.dimension, []).append(indicator)

    # -- 1. Recuperação, um par (provedor × indicador) por consulta -----------
    #
    # As consultas são montadas todas antes e buscadas de uma vez. A §4.4 exige
    # que a recuperação seja *por indicador*, e continua sendo: são as mesmas 39
    # consultas, com o mesmo texto e o mesmo filtro por provedor. O que mudou é
    # que os 39 textos viajam juntos até a API de embeddings — 17,5s de
    # ida-e-voltas sequenciais viraram 0,44s, sem tocar em nenhum vetor.
    hints = dict(query_hints or {})
    pares: List[Tuple[Mapping[str, str], IndicatorConfig, Tuple[str, ...]]] = [
        (provider, indicator, tuple(hints.get(indicator.id, ())))
        for provider in providers
        for indicator in indicators
    ]
    consultas = [
        (rag.query_for_indicator(indicator, provider.get("name"), extras), provider["id"])
        for provider, indicator, extras in pares
    ]
    resultados = await run_in_threadpool(
        rag.search_many, consultas, CHUNKS_PER_INDICATOR, session_id
    )

    chunks_por_par: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for (provider, indicator, extras), (query_text, _), hits in zip(
        pares, consultas, resultados
    ):
        chunks_por_par[(provider["id"], indicator.id)] = hits
        resultado.rag_audit.append(
            {
                "dimension": indicator.dimension,
                "indicator_id": indicator.id,
                "provider_id": provider["id"],
                "query_text": query_text,
                # Separa o que veio da pesquisa do que veio do Bloco E: sem
                # isso, a consulta gravada não diz por que ficou como ficou.
                "refined_terms": list(extras),
                "top_k": CHUNKS_PER_INDICATOR,
                "chunks": hits,
            }
        )

    # -- 2. Interpretação, uma chamada por (provedor × dimensão) --------------
    if concurrency is None:
        concurrency = get_settings().llm_concurrency
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))
    tarefas = []
    for provider in providers:
        for dimension, do_grupo in por_dimensao.items():
            reunidos = [
                chunk
                for indicator in do_grupo
                for chunk in chunks_por_par.get((provider["id"], indicator.id), [])
            ]
            contexto = _dedupe_chunks(reunidos)[:MAX_CHUNKS_PER_CALL]
            tarefas.append(
                _extract_dimension(
                    provider, dimension, do_grupo, contexto, methodology, log, semaphore
                )
            )

    respostas = await asyncio.gather(*tarefas)

    findings: List[Finding] = []
    for parte, run in respostas:
        findings.extend(parte)
        if run is not None:
            resultado.llm_runs.append(run.as_dict())

    # -- 3. Comparabilidade entre alternativas -------------------------------
    findings = list(enforce_unit_consistency(findings, methodology, log))

    resultado.findings = tuple(findings)

    # -- 4. Trechos para o relatório, vinculados ao indicador que os trouxe ---
    nomes = {i.id: i.name for i in indicators}
    for provider in providers:
        itens: List[Dict[str, Any]] = []
        vistos: set = set()
        for indicator in indicators:
            for chunk in chunks_por_par.get((provider["id"], indicator.id), []):
                chave = (indicator.id, chunk.get("chunk_id") or chunk.get("page_content"))
                if chave in vistos:
                    continue
                vistos.add(chave)
                itens.append(
                    {
                        **chunk,
                        "criterion": indicator.dimension,
                        "indicator_id": indicator.id,
                        "indicator_name": nomes.get(indicator.id, indicator.id),
                        # Os termos do Quadro 27 que estão de fato no trecho.
                        # É o que liga a citação ao indicador aos olhos de quem
                        # lê o relatório: a consulta foi montada com estes
                        # termos (§4.4), e aqui se vê quais deles o documento
                        # traz. Lista vazia é resposta legítima — o trecho veio
                        # por similaridade, não por casamento de palavra.
                        "matched_terms": rag.terms_found(
                            chunk.get("page_content") or "", indicator.search_terms
                        ),
                    }
                )
        resultado.evidences[provider["id"]] = itens

    return resultado


__all__ = [
    "CHUNKS_PER_INDICATOR",
    "DEFAULT_CONCURRENCY",
    "MAX_CHUNKS_PER_CALL",
    "PROMPT_ID",
    "ExtractionResult",
    "Finding",
    "canonical_unit",
    "describe_indicators",
    "enforce_unit_consistency",
    "extract_performances",
]
