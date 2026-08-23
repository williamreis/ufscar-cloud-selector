"""
Schemas de saída da LLM (diretriz §19).

Toda geração passa por um destes modelos antes de ser aceita. O schema é a
primeira barreira do pipeline da §25 — o que não couber aqui não vira dado.

O ponto central do `IndicatorEvidence` é o que ele **não** tem: campo de nota,
de peso ou de posição no ranking. A LLM descreve a evidência encontrada; a
conversão em valor comparável é da rubrica e da normalização, em
`domain/normalization.py`. Um modelo que quisesse pontuar um provedor não teria
onde escrever a pontuação.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

# Estados de evidência da §11, na forma que a LLM pode declarar. `INVALID` não
# está aqui de propósito: é veredito da validação, não algo que o modelo escolhe.
EVIDENCE_STATUSES = ("FOUND", "PARTIAL", "NOT_FOUND")

# Natureza da evidência (§4.4.1 da dissertação). Classificação descritiva — a
# §5.2 é explícita em não usá-la como pontuação da alternativa.
EVIDENCE_NATURES = ("quantitative", "qualitative", "insufficient")

# Textos que modelos costumam devolver no lugar de um número ausente. Mapeá-los
# para `None` normaliza uma **ausência**; não é salvar conteúdo inválido e
# transformá-lo em nota, que é o que a §25 proíbe.
_EMPTY_VALUES = {
    "",
    "-",
    "--",
    "n/a",
    "na",
    "nd",
    "n.d.",
    "null",
    "none",
    "nao informado",
    "não informado",
    "nao identificado",
    "não identificado",
    "not available",
    "not found",
    "not identified",
    "unknown",
}


class PreferenceNotes(BaseModel):
    """Saída de `PROMPT_PREFERENCE_NOTES_V1`: apenas texto, nenhum número."""

    notes: str = Field(description="Justificativa textual das prioridades declaradas.")


class IndicatorQueryHint(BaseModel):
    """
    Termos de busca que o texto do gestor sugeriu para **um** indicador (§4.5.1).

    Não há campo de peso, prioridade ou relevância aqui, e isso é o ponto: a
    §4.5.1 determina que o Bloco E não altere os pesos das dimensões nem os pesos
    locais dos indicadores. Sem campo, não há caminho.
    """

    indicator_id: str
    terms: List[str] = Field(default_factory=list)


class QueryRefinement(BaseModel):
    """
    Saída de `PROMPT_QUERY_REFINEMENT_V1`.

    Lista vazia é resposta válida: o gestor pode não ter escrito nada aplicável,
    e forçar o modelo a produzir termos nesse caso seria pedir que inventasse
    requisito.
    """

    refinements: List[IndicatorQueryHint] = Field(default_factory=list)


class IndicatorEvidence(BaseModel):
    """
    Evidência documental encontrada para **um** indicador de **um** provedor.

    Os campos são exatamente os que a §5.4 enumera como obrigatórios na saída:

        indicador analisado                     → indicator_id
        evidência identificada                  → summary
        natureza quantitativa ou qualitativa    → nature
        valor ou característica extraída        → extracted_value (+ value/unit
                                                   ou category, na forma que o
                                                   cálculo consome)
        referência à fonte documental           → source_chunk_id, source_document

    `extracted_value` e `value`/`category` coexistem de propósito. O Quadro 26
    manda extrair "o valor, característica, prática ou evidência explicitamente
    apresentada", e nem tudo o que um documento apresenta cabe num número: "ISO
    27001, ISO 27017 e SOC 2" é a característica extraída, e a categoria da
    rubrica é a leitura dela. Guardar as duas mantém o que o documento diz ao
    lado do que o modelo classificou, que é o que torna a classificação
    conferível.
    """

    indicator_id: str = Field(description="Id do indicador analisado, exatamente como fornecido.")
    evidence_status: Literal["FOUND", "PARTIAL", "NOT_FOUND"]
    nature: Literal["quantitative", "qualitative", "insufficient"]

    # "o valor ou característica extraída quando aplicável" (§5.4), na forma como
    # o documento a apresenta. É texto: não entra em conta nenhuma.
    extracted_value: Optional[str] = Field(
        default=None,
        description="Valor, característica ou prática extraída, como aparece no documento.",
    )

    # Preenchido só quando o indicador é quantitativo e o documento traz o valor.
    value: Optional[float] = Field(
        default=None, description="Valor numérico exatamente como publicado no documento."
    )
    unit: Optional[str] = Field(
        default=None, description="Unidade do valor, como aparece no documento (%, ms, ratio...)."
    )

    # Preenchido só quando o indicador é qualitativo. Precisa pertencer à
    # allowlist da rubrica; a verificação é feita depois, em `evidence.py`.
    category: Optional[str] = Field(
        default=None, description="Categoria da rubrica, escolhida entre as fornecidas."
    )

    summary: str = Field(description="Síntese da evidência, sustentada apenas pelos trechos.")

    # Rastreabilidade (§19): o identificador precisa estar entre os trechos que
    # foram efetivamente entregues ao modelo.
    source_chunk_id: Optional[str] = None
    source_document: Optional[str] = None

    @field_validator("value", mode="before")
    @classmethod
    def _blank_is_absent(cls, raw: object) -> object:
        """Texto vazio ou marcador de ausência vira `None`, não erro de validação."""
        if isinstance(raw, str) and raw.strip().lower() in _EMPTY_VALUES:
            return None
        return raw

    @field_validator(
        "category", "unit", "extracted_value", "source_chunk_id", "source_document",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, raw: object) -> object:
        if isinstance(raw, str) and not raw.strip():
            return None
        return raw


class DimensionEvidence(BaseModel):
    """
    Saída de `PROMPT_EVIDENCE_EXTRACTION_V1`: uma entrada por indicador pedido.

    Indicador ausente da lista **não** é erro de validação — é tratado como
    `NOT_FOUND` em `evidence.py`, com a lacuna registrada. Recusar a resposta
    inteira por causa de um indicador omitido descartaria as evidências válidas
    dos demais.

    A chave `findings`, porém, é obrigatória. Sem ela a resposta não é uma
    extração vazia, é outra coisa — e aceitá-la como lista vazia transformaria
    "o modelo respondeu fora do contrato" em "não há evidência", que são
    diagnósticos diferentes para o gestor.
    """

    findings: List[IndicatorEvidence]


__all__ = [
    "EVIDENCE_NATURES",
    "EVIDENCE_STATUSES",
    "DimensionEvidence",
    "IndicatorEvidence",
    "IndicatorQueryHint",
    "PreferenceNotes",
    "QueryRefinement",
]
