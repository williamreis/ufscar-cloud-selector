"""
Carga e validação da configuração metodológica (diretriz §39 e §45).

Indicadores e escalas vivem em JSON, fora do código, porque a diretriz determina
que os pontos ainda abertos na dissertação sejam externalizados — "implementar de
forma configurável", não decidir por conta própria.

Duas garantias que este módulo dá ao resto do domínio:

  - **o que chega às regras de negócio já está validado.** Indicador sem
    dimensão conhecida, direção inválida, rubrica inexistente ou `question_id`
    duplicado falha aqui, no carregamento, e não vira peso mais adiante.

  - **a configuração é versionável.** `fingerprint()` devolve o hash canônico dos
    dois arquivos, que entra no bloco de versões de cada avaliação — mudar um
    coeficiente muda a versão, sem reescrever o passado.

Nada aqui interpreta o significado metodológico dos valores. Se `irrelevante`
vale 1 ou 0 é decisão da dissertação; o código só precisa que o valor exista e
seja um número (ou nulo).
"""

import json
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from audit.versioning import canonical_hash
from config import get_settings

logger = logging.getLogger("uvicorn.error")

DIRECTION_BENEFIT = "benefit"
DIRECTION_MINIMIZE = "minimize"
VALID_DIRECTIONS = (DIRECTION_BENEFIT, DIRECTION_MINIMIZE)

TYPE_QUANTITATIVE = "quantitative"
TYPE_QUALITATIVE = "qualitative"
VALID_TYPES = (TYPE_QUANTITATIVE, TYPE_QUALITATIVE)


class MethodologyConfigError(ValueError):
    """Configuração metodológica inválida — impede que o sistema suba com regra quebrada."""


@dataclass(frozen=True)
class Rubric:
    """
    Rubrica de um indicador qualitativo — o Quadro 23 da dissertação.

    `categories` mapeia nível de atendimento → valor. **O valor pode ser nulo**,
    e essa é a linha "Não identificado" do quadro, que registra "—" e não zero:

        "O nível 'Não identificado' não recebe valor igual a zero, uma vez que a
        ausência de evidência não é interpretada como desempenho inferior do
        provedor. Nessa situação, aplica-se o procedimento definido na Seção
        4.4.1.3 para informações ausentes."

    Por isso `value_for` não basta para decidir o que fazer com uma categoria:
    `None` significa duas coisas diferentes (categoria desconhecida × categoria
    conhecida sem valor), e quem precisa distingui-las usa `is_allowed`.

    `conditions` é a coluna "Condição da evidência". Não entra em cálculo — é o
    texto que o prompt de extração entrega à LLM para que ela classifique pela
    regra da pesquisa, e não por um rótulo solto.
    """

    name: str
    mode: str
    categories: Mapping[str, Optional[float]]
    conditions: Mapping[str, str] = field(default_factory=dict)

    def is_allowed(self, category: Optional[str]) -> bool:
        """A categoria pertence à allowlist da rubrica (§19)?"""
        return category is not None and category in self.categories

    def value_for(self, category: Optional[str]) -> Optional[float]:
        """
        Valor numérico de uma categoria.

        Devolve `None` tanto para categoria desconhecida quanto para categoria
        sem valor ("Não identificado"). Use `is_allowed` para separar os casos.
        """
        if category is None:
            return None
        return self.categories.get(category)

    def condition_for(self, category: str) -> Optional[str]:
        return self.conditions.get(category)

    @property
    def allowed_categories(self) -> Tuple[str, ...]:
        return tuple(self.categories)

    @property
    def scored_categories(self) -> Tuple[str, ...]:
        """Categorias que produzem valor — as que o Quadro 23 pontua."""
        return tuple(k for k, v in self.categories.items() if v is not None)


@dataclass(frozen=True)
class IndicatorConfig:
    """Definição declarativa de um indicador (§39)."""

    id: str
    dimension: str
    name: str
    data_type: str
    question_id: Optional[str] = None
    direction: Optional[str] = None
    expected_units: Tuple[str, ...] = ()
    search_terms: Tuple[str, ...] = ()
    rubric: Optional[Rubric] = None
    notes: Optional[str] = None
    pending_decision: Optional[str] = None
    # Linhas dos Quadros 22 e 24 que este indicador operacionaliza, e o exemplo
    # de evidência da coluna correspondente do Quadro 24. Não entra em cálculo:
    # existe para que os quadros da dissertação possam ser conferidos contra o
    # conjunto que o produto de fato avalia.
    quadro_22: Tuple[str, ...] = ()
    quadro_24: Tuple[str, ...] = ()
    evidence_example: Optional[str] = None
    # Frase que vai para o prompt dizendo QUAL grandeza do documento é a certa,
    # quando o relatório publica mais de uma que casa com os termos de busca.
    # O caso concreto: o Google divulga "100% de correspondência renovável" e
    # também a capacidade contratada em GW; o extrator trazia os 22 GW, que a
    # guarda de unidades recusava — evidência real, grandeza errada. Não altera
    # cálculo nenhum: é desambiguação de leitura.
    evidence_hint: Optional[str] = None

    @property
    def is_quantitative(self) -> bool:
        return self.data_type == TYPE_QUANTITATIVE

    @property
    def is_qualitative(self) -> bool:
        return self.data_type == TYPE_QUALITATIVE

    def as_dict(self) -> Dict[str, Any]:
        return {
            "indicator_id": self.id,
            "dimension": self.dimension,
            "name": self.name,
            "data_type": self.data_type,
            "direction": self.direction,
            "question_id": self.question_id,
            "expected_units": list(self.expected_units),
            "rubric": self.rubric.name if self.rubric else None,
            "pending_decision": self.pending_decision,
            "quadro_22": list(self.quadro_22),
            "quadro_24": list(self.quadro_24),
            "evidence_example": self.evidence_example,
            "evidence_hint": self.evidence_hint,
        }


@dataclass(frozen=True)
class ExcludedIndicator:
    """
    Linha dos Quadros 22/24 que não virou indicador da plataforma (§4.4).

    Existe para que "ficou de fora" seja uma afirmação com justificativa e não um
    silêncio. O `reason` vem de um vocabulário fechado, derivado dos critérios que
    a §4.4 enumera — disponibilidade, verificação documental e comparabilidade.
    """

    name: str
    dimension: str
    source: str
    reason: str
    note: Optional[str] = None
    # A mesma linha aparece com nomes diferentes nos dois quadros — "Rendimento e
    # Eficiência (throughput)" no 22 e "Throughput" no 24. Sem os apelidos, a
    # conferência acusaria falta de registro onde há só diferença de redação.
    aliases: Tuple[str, ...] = ()

    @property
    def all_names(self) -> Tuple[str, ...]:
        return (self.name, *self.aliases)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "dimension": self.dimension,
            "source": self.source,
            "reason": self.reason,
            "note": self.note,
        }


@dataclass(frozen=True)
class Methodology:
    """Configuração metodológica completa, já validada."""

    indicators: Tuple[IndicatorConfig, ...]
    dimensions: Tuple[str, ...]
    dimension_names: Mapping[str, str]
    relevance_coefficients: Mapping[str, Optional[float]]
    relevance_labels: Mapping[str, str]
    rubrics: Mapping[str, Rubric]
    ahp_weight_method: str
    ahp_consistency_threshold: float
    ahp_random_index: Mapping[int, float]
    tie_break_policy: str
    tie_break_tolerance: float
    partial_counts_as_comparable: bool
    #: Diferença máxima, em anos, entre a evidência mais antiga e a mais recente
    #: de um indicador. `None` desliga a regra. Ver `_todo_vintage` no scales.json.
    vintage_tolerance_years: Optional[int]
    #: Retira da Equação 5 o indicador cujo valor normalizado é o mesmo para
    #: todas as alternativas. Ver `comparability` no scales.json.
    exclude_non_discriminative: bool
    indicators_version: str
    not_operationalized: Tuple[ExcludedIndicator, ...] = ()
    exclusion_reasons: Mapping[str, str] = field(default_factory=dict)
    sources: Mapping[str, str] = field(default_factory=dict)
    hashes: Mapping[str, Optional[str]] = field(default_factory=dict)

    # -- Consultas -----------------------------------------------------------

    def by_id(self, indicator_id: str) -> IndicatorConfig:
        for indicator in self.indicators:
            if indicator.id == indicator_id:
                return indicator
        raise KeyError(f"Indicador não configurado: {indicator_id}.")

    def by_dimension(self, dimension: str) -> Tuple[IndicatorConfig, ...]:
        return tuple(i for i in self.indicators if i.dimension == dimension)

    def dimension_name(self, dimension: str) -> str:
        """Nome legível da dimensão; cai no próprio id quando não há rótulo."""
        return self.dimension_names.get(dimension, dimension)

    def by_question(self, question_id: str) -> Optional[IndicatorConfig]:
        for indicator in self.indicators:
            if indicator.question_id == question_id:
                return indicator
        return None

    def coefficient_for_label(self, label: Optional[str]) -> Tuple[Optional[float], bool]:
        """
        Coeficiente de relevância de uma alternativa do questionário.

        Devolve `(valor, reconhecida)`. A distinção importa: uma alternativa
        reconhecida cujo coeficiente é nulo é a resposta "não sei" (§5.1) e deve
        ser registrada como `null`; uma alternativa **não** reconhecida é outra
        coisa — pergunta que não é de relevância — e não entra no cálculo.
        """
        if label is None:
            return None, False
        key = self.relevance_labels.get(label)
        if key is None:
            return None, False
        return self.relevance_coefficients.get(key), True

    def quadro_coverage(self) -> Dict[str, Tuple[str, ...]]:
        """
        Linhas dos Quadros 22 e 24 cobertas pelo conjunto operacional.

        Serve à conferência dos quadros da dissertação: o que está aqui é o que o
        produto avalia; o que está em `not_operationalized` é o que ficou fora,
        com motivo. Uma linha em nenhum dos dois é uma lacuna de registro.
        """
        q22: List[str] = []
        q24: List[str] = []
        for indicator in self.indicators:
            q22.extend(indicator.quadro_22)
            q24.extend(indicator.quadro_24)
        return {
            "quadro_22": tuple(dict.fromkeys(q22)),
            "quadro_24": tuple(dict.fromkeys(q24)),
        }

    def fingerprint(self) -> Dict[str, Any]:
        """Bloco de versão da configuração metodológica, para o registro da avaliação."""
        return {
            "indicators_version": self.indicators_version,
            "indicators_hash": self.hashes.get("indicators"),
            "scales_hash": self.hashes.get("scales"),
            "indicator_count": len(self.indicators),
            "ahp_weight_method": self.ahp_weight_method,
        }


# ---------------------------------------------------------------------------
# Carregamento
# ---------------------------------------------------------------------------


def _read_json(path: Path, description: str) -> Dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise MethodologyConfigError(
            f"{description} não pôde ser lido em {path}: {exc}."
        ) from exc
    except json.JSONDecodeError as exc:
        raise MethodologyConfigError(f"{description} não é JSON válido ({path}): {exc}.") from exc


def _build_rubrics(raw: Mapping[str, Any]) -> Dict[str, Rubric]:
    rubrics: Dict[str, Rubric] = {}
    for name, definition in raw.items():
        # Chaves de anotação (_todo, status, _nota) convivem com as rubricas.
        if name.startswith("_") or not isinstance(definition, dict):
            continue
        categories = definition.get("categories")
        if not isinstance(categories, dict) or not categories:
            continue
        valores: Dict[str, Optional[float]] = {}
        for chave, valor in categories.items():
            if valor is None:
                # Linha "Não identificado" do Quadro 23: registra "—", não zero.
                valores[str(chave)] = None
                continue
            try:
                valores[str(chave)] = float(valor)
            except (TypeError, ValueError) as exc:
                raise MethodologyConfigError(
                    f"Rubrica {name!r}: categoria {chave!r} tem valor não numérico ({valor!r})."
                ) from exc

        if not any(v is not None for v in valores.values()):
            raise MethodologyConfigError(
                f"Rubrica {name!r} não tem nenhuma categoria com valor: nada seria pontuável."
            )

        condicoes = definition.get("conditions") or {}
        desconhecidas = set(condicoes) - set(valores)
        if desconhecidas:
            raise MethodologyConfigError(
                f"Rubrica {name!r}: `conditions` descreve categorias inexistentes: "
                f"{', '.join(sorted(desconhecidas))}."
            )

        rubrics[name] = Rubric(
            name=name,
            mode=str(definition.get("mode", "ordinal")),
            categories=valores,
            conditions={str(k): str(v) for k, v in condicoes.items()},
        )
    if not rubrics:
        raise MethodologyConfigError("Nenhuma rubrica válida em default_rubrics.")
    return rubrics


def _build_indicators(
    raw: List[Any],
    dimensions: Tuple[str, ...],
    rubrics: Mapping[str, Rubric],
) -> Tuple[IndicatorConfig, ...]:
    indicators: List[IndicatorConfig] = []
    seen_ids: set = set()
    seen_questions: set = set()

    for entry in raw:
        if not isinstance(entry, dict):
            raise MethodologyConfigError("Entrada de indicador não é um objeto.")

        indicator_id = entry.get("id")
        if not indicator_id:
            raise MethodologyConfigError("Indicador sem `id`.")
        if indicator_id in seen_ids:
            raise MethodologyConfigError(f"Indicador duplicado: {indicator_id}.")
        seen_ids.add(indicator_id)

        dimension = entry.get("dimension")
        if dimension not in dimensions:
            raise MethodologyConfigError(
                f"{indicator_id}: dimensão {dimension!r} não está declarada em `dimensions`."
            )

        data_type = entry.get("data_type")
        if data_type not in VALID_TYPES:
            raise MethodologyConfigError(
                f"{indicator_id}: data_type {data_type!r} inválido (use {VALID_TYPES})."
            )

        direction = entry.get("direction")
        rubric: Optional[Rubric] = None

        if data_type == TYPE_QUANTITATIVE:
            if direction not in VALID_DIRECTIONS:
                raise MethodologyConfigError(
                    f"{indicator_id}: indicador quantitativo exige direction "
                    f"{VALID_DIRECTIONS}, recebeu {direction!r}."
                )
        else:
            # Qualitativo não normaliza por máximo/mínimo — a rubrica é que dá o valor.
            if direction is not None:
                raise MethodologyConfigError(
                    f"{indicator_id}: indicador qualitativo não usa `direction`."
                )
            rubric_name = entry.get("rubric")
            if rubric_name not in rubrics:
                raise MethodologyConfigError(
                    f"{indicator_id}: rubrica {rubric_name!r} não existe em default_rubrics."
                )
            rubric = rubrics[rubric_name]

        cobertura = entry.get("coverage") or {}
        if not isinstance(cobertura, dict):
            raise MethodologyConfigError(f"{indicator_id}: `coverage` não é um objeto.")

        question_id = entry.get("question_id")
        if question_id:
            if question_id in seen_questions:
                raise MethodologyConfigError(
                    f"{indicator_id}: question_id {question_id!r} já está em outro indicador."
                )
            seen_questions.add(question_id)

        indicators.append(
            IndicatorConfig(
                id=indicator_id,
                dimension=dimension,
                name=entry.get("name") or indicator_id,
                data_type=data_type,
                question_id=question_id,
                direction=direction,
                expected_units=tuple(entry.get("expected_units") or ()),
                search_terms=tuple(entry.get("search_terms") or ()),
                rubric=rubric,
                notes=entry.get("_nota"),
                pending_decision=entry.get("_todo"),
                quadro_22=tuple(cobertura.get("quadro_22") or ()),
                quadro_24=tuple(cobertura.get("quadro_24") or ()),
                evidence_example=cobertura.get("evidence_example"),
                evidence_hint=entry.get("evidence_hint"),
            )
        )

    if not indicators:
        raise MethodologyConfigError("Nenhum indicador configurado.")
    return tuple(indicators)


def _build_exclusions(
    raw: Mapping[str, Any],
    indicators: Tuple[IndicatorConfig, ...],
) -> Tuple[Tuple[ExcludedIndicator, ...], Dict[str, str]]:
    """
    Valida o registro do que ficou fora do conjunto operacional (§4.4).

    Duas checagens, e as duas existem porque este bloco só vale se estiver certo:

      - **motivo fora do vocabulário falha.** Um motivo livre transformaria a
        justificativa exigida pela §4.4 em texto solto que ninguém confere.

      - **linha excluída que também está coberta falha.** Se um indicador
        operacionaliza "Confiabilidade" e a lista diz que ela ficou de fora, uma
        das duas afirmações é falsa, e a configuração não pode carregar sem que se
        saiba qual.
    """
    motivos = {str(k): str(v) for k, v in (raw.get("_motivos") or {}).items()}
    itens = raw.get("items") or []
    if not isinstance(itens, list):
        raise MethodologyConfigError("not_operationalized.items não é uma lista.")

    cobertos = {nome.casefold() for i in indicators for nome in (*i.quadro_22, *i.quadro_24)}
    excluidos: List[ExcludedIndicator] = []

    for entry in itens:
        if not isinstance(entry, dict):
            raise MethodologyConfigError("Entrada de not_operationalized não é um objeto.")
        nome = entry.get("name")
        if not nome:
            raise MethodologyConfigError("Entrada de not_operationalized sem `name`.")

        motivo = entry.get("reason")
        if motivos and motivo not in motivos:
            raise MethodologyConfigError(
                f"not_operationalized[{nome!r}]: motivo {motivo!r} fora do vocabulário "
                f"({', '.join(sorted(motivos))})."
            )

        apelidos = tuple(str(a) for a in (entry.get("aliases") or ()))
        for rotulo in (str(nome), *apelidos):
            if rotulo.casefold() in cobertos:
                raise MethodologyConfigError(
                    f"not_operationalized[{nome!r}]: {rotulo!r} aparece na cobertura de um "
                    "indicador. Uma das duas afirmações está errada."
                )

        excluidos.append(
            ExcludedIndicator(
                name=str(nome),
                dimension=str(entry.get("dimension") or ""),
                source=str(entry.get("source") or ""),
                reason=str(motivo or ""),
                note=entry.get("_nota"),
                aliases=apelidos,
            )
        )

    return tuple(excluidos), motivos


def load_methodology(
    indicators_path: Optional[Path] = None,
    scales_path: Optional[Path] = None,
) -> Methodology:
    """Lê, valida e devolve a configuração metodológica."""
    settings = get_settings()
    indicators_path = Path(indicators_path or settings.indicators_path)
    scales_path = Path(scales_path or settings.methodology_scales_path)

    indicators_raw = _read_json(indicators_path, "Configuração de indicadores")
    scales_raw = _read_json(scales_path, "Configuração de escalas")

    dimensions_raw = indicators_raw.get("dimensions") or {}
    dimensions = tuple(k for k in dimensions_raw if not k.startswith("_"))
    if not dimensions:
        raise MethodologyConfigError("Nenhuma dimensão declarada em indicators.json.")

    rubrics = _build_rubrics(scales_raw.get("default_rubrics") or {})
    indicators = _build_indicators(indicators_raw.get("indicators") or [], dimensions, rubrics)

    relevance = scales_raw.get("relevance_coefficients") or {}
    values = relevance.get("values") or {}
    if not values:
        raise MethodologyConfigError("relevance_coefficients.values está vazio.")
    coefficients: Dict[str, Optional[float]] = {}
    for key, value in values.items():
        if value is None:
            coefficients[key] = None
            continue
        try:
            coefficients[key] = float(value)
        except (TypeError, ValueError) as exc:
            raise MethodologyConfigError(
                f"Coeficiente de relevância {key!r} não é numérico: {value!r}."
            ) from exc

    labels = {str(k): str(v) for k, v in (relevance.get("labels") or {}).items()}
    desconhecidas = {v for v in labels.values()} - set(coefficients)
    if desconhecidas:
        raise MethodologyConfigError(
            f"Rótulos apontam para coeficientes inexistentes: {', '.join(sorted(desconhecidas))}."
        )

    ahp = scales_raw.get("ahp") or {}
    weight_method = str(ahp.get("weight_method", "column_mean"))
    if weight_method not in ("column_mean", "eigenvector"):
        raise MethodologyConfigError(
            f"ahp.weight_method inválido: {weight_method!r} (use column_mean ou eigenvector)."
        )
    random_index = {int(k): float(v) for k, v in (ahp.get("random_index") or {}).items()}
    if not random_index:
        raise MethodologyConfigError("ahp.random_index está vazio.")

    tie_break = scales_raw.get("tie_break") or {}
    evidence = scales_raw.get("evidence") or {}
    comparability = scales_raw.get("comparability") or {}

    excluidos, motivos = _build_exclusions(
        indicators_raw.get("not_operationalized") or {}, indicators
    )

    return Methodology(
        indicators=indicators,
        dimensions=dimensions,
        dimension_names={
            key: str((dimensions_raw.get(key) or {}).get("name") or key) for key in dimensions
        },
        relevance_coefficients=coefficients,
        relevance_labels=labels,
        rubrics=rubrics,
        ahp_weight_method=weight_method,
        ahp_consistency_threshold=float(ahp.get("consistency_threshold", 0.10)),
        ahp_random_index=random_index,
        tie_break_policy=str(tie_break.get("policy", "show_tie")),
        tie_break_tolerance=float(tie_break.get("tolerance", 1e-9)),
        partial_counts_as_comparable=bool(evidence.get("partial_counts_as_comparable", False)),
        vintage_tolerance_years=(
            None
            if evidence.get("vintage_tolerance_years") is None
            else int(evidence["vintage_tolerance_years"])
        ),
        exclude_non_discriminative=bool(
            comparability.get("exclude_non_discriminative", False)
        ),
        indicators_version=str(indicators_raw.get("version", "1")),
        not_operationalized=excluidos,
        exclusion_reasons=motivos,
        sources={"indicators": str(indicators_path), "scales": str(scales_path)},
        hashes={
            "indicators": canonical_hash(indicators_raw),
            "scales": canonical_hash(scales_raw),
        },
    )


@lru_cache(maxsize=1)
def _cached() -> Methodology:
    return load_methodology()


def get_methodology() -> Methodology:
    """Configuração em vigor, em cache. `reload_methodology()` descarta o cache."""
    return _cached()


def reload_methodology() -> Methodology:
    _cached.cache_clear()
    return _cached()


__all__ = [
    "DIRECTION_BENEFIT",
    "ExcludedIndicator",
    "DIRECTION_MINIMIZE",
    "TYPE_QUALITATIVE",
    "TYPE_QUANTITATIVE",
    "IndicatorConfig",
    "Methodology",
    "MethodologyConfigError",
    "Rubric",
    "get_methodology",
    "load_methodology",
    "reload_methodology",
]
