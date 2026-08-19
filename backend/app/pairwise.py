"""
Comparações par a par do bloco D: modelagem semântica da resposta e conversão
determinística para a escala de Saaty.

O gestor não informa mais um número nem escolhe entre nove frases prontas. Ele
responde duas coisas:

  1. **qual dimensão tem maior prioridade** — a da esquerda, a da direita, ou
     nenhuma ("igual importância");
  2. **com que intensidade** — moderadamente, fortemente, muito fortemente ou
     extremamente (a pergunta só existe quando há uma dimensão preferida).

Essas duas informações são o que trafega na API e o que fica gravado. A razão de
Saaty e o valor da célula da matriz são **derivados aqui**, no servidor, e nunca
aceitos prontos do cliente: um payload com `ahp_value: 500` não tem por onde
entrar, porque não existe campo que o carregue.

    preferência = esquerda  →  a(esquerda, direita) = intensidade
    preferência = direita   →  a(esquerda, direita) = 1 / intensidade
    preferência = igual     →  a(esquerda, direita) = 1

A reciprocidade (a_ji = 1/a_ij) e a diagonal unitária continuam sendo montadas em
`ahp.judgments_to_pairwise_matrix` — este módulo só produz a razão a_ij de cada
par, exatamente como a versão anterior fazia a partir do texto da alternativa.
"""

from typing import Any, Dict, Optional, Tuple

# Alternativa de indiferença: não tem intensidade associada, por definição.
EQUAL = "equal"

# Escala verbal → intensidade de Saaty. É a única tabela de conversão do sistema;
# a interface nunca mostra estes números ao gestor.
INTENSITY_SCALE: Dict[str, float] = {
    "moderate": 3.0,
    "strong": 5.0,
    "very_strong": 7.0,
    "extreme": 9.0,
}

# Advérbio correspondente, para reconstruir a frase legível na memória de cálculo.
INTENSITY_ADVERBS: Dict[str, str] = {
    "moderate": "moderadamente",
    "strong": "fortemente",
    "very_strong": "muito fortemente",
    "extreme": "extremamente",
}

# Nome de exibição das dimensões. Duplica o rótulo do questions.json de propósito:
# a descrição legível do julgamento é gravada no registro de auditoria, e o
# backend não lê o questionário — se o rótulo mudar lá, os registros antigos
# continuam descrevendo o que foi perguntado na época.
DIMENSION_LABELS: Dict[str, str] = {
    "sustainability": "Sustentabilidade",
    "performance": "Desempenho Operacional",
    "security": "Segurança da Informação",
}


class PairwiseError(ValueError):
    """Resposta par a par incoerente — impede que um julgamento inválido vire peso."""


def dimension_label(criterion: str) -> str:
    return DIMENSION_LABELS.get(criterion, criterion)


def validate(
    left: str,
    right: str,
    preference: Optional[str],
    intensity: Optional[str],
    valid_criteria: Optional[Tuple[str, ...]] = None,
) -> None:
    """
    Regras de completude de uma comparação (as mesmas que a interface aplica antes
    de deixar avançar):

      - `preference` é a dimensão da esquerda, a da direita ou "equal";
      - com "equal", `intensity` **precisa** ser nula — "igual + moderadamente"
        não é um estado metodologicamente válido, é uma resposta pela metade
        trocada de dono;
      - com uma dimensão preferida, `intensity` é obrigatória e tem de estar na
        escala verbal.
    """
    if not left or not right or left == right:
        raise PairwiseError(f"Par inválido: left={left!r}, right={right!r}.")
    if valid_criteria is not None:
        for side in (left, right):
            if side not in valid_criteria:
                raise PairwiseError(f"Dimensão desconhecida no par: {side!r}.")

    if preference not in (EQUAL, left, right):
        raise PairwiseError(
            f"Preferência {preference!r} não corresponde a {left!r}, {right!r} nem a {EQUAL!r}."
        )

    if preference == EQUAL:
        if intensity is not None:
            raise PairwiseError(
                "Igual importância não admite intensidade — a razão de Saaty já é 1."
            )
        return

    if intensity is None:
        raise PairwiseError(
            f"Informe a intensidade da preferência por {dimension_label(preference)}."
        )
    if intensity not in INTENSITY_SCALE:
        raise PairwiseError(f"Intensidade desconhecida: {intensity!r}.")


def saaty_intensity(preference: Optional[str], intensity: Optional[str]) -> float:
    """Intensidade da escala de Saaty: 1 na indiferença, 3/5/7/9 nas demais."""
    if preference == EQUAL:
        return 1.0
    return INTENSITY_SCALE[intensity]


def matrix_value(
    left: str,
    right: str,
    preference: Optional[str],
    intensity: Optional[str],
) -> float:
    """
    Razão a_ij da célula (esquerda, direita).

    A direção é o que decide entre a intensidade e o seu recíproco: a mesma
    resposta "fortemente" vale 5 quando a preferida é a dimensão da esquerda e
    1/5 quando é a da direita.
    """
    value = saaty_intensity(preference, intensity)
    if preference == right:
        return 1.0 / value
    return value


def describe(
    left: str,
    right: str,
    preference: Optional[str],
    intensity: Optional[str],
) -> str:
    """
    Frase legível equivalente à resposta estruturada — é ela que aparece na
    memória de cálculo, na lista de respostas do relatório e no registro de
    auditoria, no lugar da alternativa que o gestor escolhia antes.

        preference = security, intensity = strong
        → "Segurança da Informação é fortemente mais importante que Sustentabilidade"
    """
    if preference == EQUAL:
        return f"{dimension_label(left)} e {dimension_label(right)} possuem igual importância"
    other = right if preference == left else left
    return (
        f"{dimension_label(preference)} é {INTENSITY_ADVERBS[intensity]} "
        f"mais importante que {dimension_label(other)}"
    )


def resolve(
    left: str,
    right: str,
    preference: Optional[str],
    intensity: Optional[str],
    valid_criteria: Optional[Tuple[str, ...]] = None,
) -> Dict[str, Any]:
    """
    Resposta estruturada completa de uma comparação, já validada: o que o gestor
    respondeu (preferência e intensidade) e o que isso significa no AHP
    (intensidade de Saaty e valor da célula), mais a descrição legível.
    """
    validate(left, right, preference, intensity, valid_criteria)
    return {
        "left_dimension": left,
        "right_dimension": right,
        "preference": preference,
        "intensity": intensity,
        "saaty_intensity": saaty_intensity(preference, intensity),
        "matrix_value": matrix_value(left, right, preference, intensity),
        "description": describe(left, right, preference, intensity),
    }


# ---------------------------------------------------------------------------
# Compatibilidade com as respostas no formato antigo (alternativa em texto).
#
# Até a versão anterior o bloco D era uma lista de nove frases prontas, e a
# resposta gravada era a frase escolhida. Envios antigos continuam no banco e a
# área de gestão os reexibe, então a leitura daquele formato permanece — o que
# saiu foi a *coleta*, não a capacidade de interpretar o que já foi coletado.
# ---------------------------------------------------------------------------

# A ordem importa: "muito fortemente" contém "fortemente", e seria capturado por ele.
_LEGACY_VERBAL_SCALE: Tuple[Tuple[str, str], ...] = (
    ("extremamente mais importante", "extreme"),
    ("muito fortemente mais importante", "very_strong"),
    ("fortemente mais importante", "strong"),
    ("moderadamente mais importante", "moderate"),
)

# Trecho que identifica a alternativa de indiferença no formato antigo.
_LEGACY_EQUAL_MARKER = "igual importância"

# Nome da dimensão no enunciado (à esquerda do "é ... mais importante") → critério.
_LEGACY_DIMENSION_NAMES: Dict[str, str] = {
    "sustentabilidade": "sustainability",
    "desempenho operacional": "performance",
    "segurança da informação": "security",
}


def from_legacy_choice(
    choice: Optional[str],
    pair: Tuple[str, str],
) -> Optional[Dict[str, Any]]:
    """
    Traduz a alternativa em texto do formato antigo na mesma resposta estruturada
    de hoje, para que um envio gravado antes da mudança produza exatamente a
    razão que produzia. Devolve None quando o texto não corresponde a nenhuma
    alternativa conhecida — melhor não ter julgamento do que inventar um.
    """
    text = (choice or "").strip().lower()
    if not text:
        return None

    left, right = pair
    if _LEGACY_EQUAL_MARKER in text:
        return resolve(left, right, EQUAL, None)

    intensity = next((name for phrase, name in _LEGACY_VERBAL_SCALE if phrase in text), None)
    if intensity is None:
        return None

    # A dimensão dominante é a que abre a frase ("<Dimensão> é ... mais importante que ...").
    leading = text.split(" é ", 1)[0].strip()
    preference = _LEGACY_DIMENSION_NAMES.get(leading)
    if preference not in (left, right):
        return None
    return resolve(left, right, preference, intensity)
