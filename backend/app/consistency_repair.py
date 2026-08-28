"""
Saídas possíveis quando as comparações do bloco D se contradizem.

A porta da consistência (§4.2.3) continua fechada: com CR acima do limite a
avaliação não prossegue. O que este módulo acrescenta é **para onde ir** — sem
decidir pelo gestor.

Motivo. Com três dimensões, a consistência perfeita exige `a13 = a12 × a23`, e o
produto estoura o teto 9 da escala de Saaty quase imediatamente: "Sustentabilidade
fortemente sobre Desempenho" mais "Segurança fortemente sobre Sustentabilidade"
pediria 1/25 na terceira comparação, que não existe na escala. Nessa situação não
há resposta correta a dar na pergunta restante, e "revise as comparações" manda o
gestor procurar o que não existe.

**O que é uma revisão pequena.** Não é distância no eixo de opções. Atravessar o
"igual" inverte qual dimensão vence, e inverter não é corrigir: é afirmar o
contrário do que o gestor afirmou. Dentro de cada comparação a ordem é semântica
antes de numérica — mexer só na intensidade, depois abandonar a preferência
("igual"), e só por último invertê-la; a menor distância em passos desempata.

**O gestor escolhe qual julgamento rever.** O módulo devolve uma lista de opções,
cada uma mexendo em uma única comparação, em vez de um único caminho a seguir. A
varredura das 729 combinações mostra que sempre há mais de uma: nos 462 casos sem
ciclo, ao menos duas das três comparações têm conserto isolado. Apresentar uma só
faria o sistema eleger de qual julgamento o gestor deve abrir mão.

**Ciclo é outra conversa.** Quando as respostas formam A > B > C > A, nenhuma
combinação de pesos honra as três — a contradição é lógica, não de intensidade, e
alguma preferência terá de ceder. Aí o diagnóstico diz isso com todas as letras
(`cycle`), em vez de entregar um remendo aritmético como se fosse ajuste fino.

**Sugerir não é aplicar.** Nada aqui altera o envio. As opções viajam no corpo do
409; só passam a valer se o gestor adotar uma na tela e reenviar. O registro de
auditoria grava o que ele enviou, nunca o que foi proposto.
"""

from itertools import product
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

import pairwise
from ahp import judgments_to_pairwise_matrix, priority_vector

# Acima disso a enumeração deixa de ser trivial (9^5 = 59.049 matrizes) e o custo
# não se justifica dentro de uma requisição. O bloco D da pesquisa tem três pares.
MAX_PARES_ENUMERAVEIS = 4

# Quantas opções no máximo devolver: uma por comparação já cobre a escolha de
# "qual julgamento eu reviso", e mais do que isso vira menu.
MAX_OPCOES = 3

_INTENSIDADES_CRESCENTES = ("moderate", "strong", "very_strong", "extreme")

# Posição da indiferença no eixo de nove alternativas. À esquerda dela vence a
# dimensão da direita; à direita, a da esquerda. É a fronteira que separa
# "abrandar o julgamento" de "inverter o julgamento".
_INDICE_IGUAL = 4


def _eixo(left: str, right: str) -> List[Tuple[Optional[str], Optional[str]]]:
    """As nove alternativas do par, na ordem em que a interface as apresenta."""
    opcoes: List[Tuple[Optional[str], Optional[str]]] = [
        (right, i) for i in reversed(_INTENSIDADES_CRESCENTES)
    ]
    opcoes.append((pairwise.EQUAL, None))
    opcoes.extend((left, i) for i in _INTENSIDADES_CRESCENTES)
    return opcoes


def _tipo_de_mudanca(atual: int, novo: int) -> str:
    """
    Natureza da alteração proposta para uma comparação.

    As três não custam a mesma coisa a quem respondeu: baixar a intensidade mantém
    o que ele disse, ir para "igual" abandona a preferência, e passar para o outro
    lado afirma o contrário do que ele afirmou.
    """
    if atual == novo:
        return "unchanged"
    if atual == _INDICE_IGUAL or novo == _INDICE_IGUAL:
        return "preference"
    if (atual < _INDICE_IGUAL) != (novo < _INDICE_IGUAL):
        return "inversion"
    return "intensity"


def _indice_atual(
    eixo: Sequence[Tuple[Optional[str], Optional[str]]],
    judgment: Dict[str, Any],
    left: str,
    right: str,
) -> Optional[int]:
    """
    Posição da resposta do gestor no eixo.

    Envios antigos (formato de frase pronta) podem não trazer preferência e
    intensidade separadas; nesse caso a posição é reconstruída pela razão
    registrada, que é a mesma informação vista do outro lado.
    """
    preference = judgment.get("preference")
    intensity = judgment.get("intensity")
    if preference is not None:
        try:
            return eixo.index((preference, intensity))
        except ValueError:
            pass

    ratio = judgment.get("ratio")
    if ratio is None or float(ratio) <= 0:
        return None
    # Proximidade em log: a escala é multiplicativa, e 9 dista de 7 o mesmo que
    # 1/9 dista de 1/7.
    razoes = [pairwise.matrix_value(left, right, p, i) for p, i in eixo]
    alvo = float(ratio)
    return min(range(len(razoes)), key=lambda k: abs(np.log(razoes[k] / alvo)))


def _ciclo_de_preferencias(
    chaves: Sequence[str],
    lados: Sequence[Tuple[str, str]],
    eixos: Sequence[Sequence[Tuple[Optional[str], Optional[str]]]],
    atuais: Sequence[int],
) -> Optional[Dict[str, Any]]:
    """
    Ciclo nas preferências declaradas (A > B, B > C, C > A), se houver.

    É a contradição que nenhuma escolha de intensidade resolve: não existe vetor
    de pesos em que as três afirmações sejam verdadeiras ao mesmo tempo. Detectar
    isso separadamente importa porque o conselho é outro — não "ajuste a força",
    e sim "uma destas três preferências precisa ceder; qual?".
    """
    arestas: Dict[str, List[str]] = {}
    frases: Dict[Tuple[str, str], str] = {}
    for n, chave in enumerate(chaves):
        left, right = lados[n]
        preferencia, intensidade = eixos[n][atuais[n]]
        if preferencia is None or preferencia == pairwise.EQUAL:
            continue
        perdedor = right if preferencia == left else left
        arestas.setdefault(preferencia, []).append(perdedor)
        frases[(preferencia, perdedor)] = pairwise.describe(
            left, right, preferencia, intensidade
        )

    caminho: List[str] = []
    visitados: set = set()

    def busca(no: str) -> Optional[List[str]]:
        if no in caminho:
            return caminho[caminho.index(no):] + [no]
        if no in visitados:
            return None
        visitados.add(no)
        caminho.append(no)
        for seguinte in arestas.get(no, []):
            achado = busca(seguinte)
            if achado:
                return achado
        caminho.pop()
        return None

    for origem in list(arestas):
        encontrado = busca(origem)
        if encontrado:
            return {
                "dimensions": encontrado[:-1],
                "statements": [
                    frases[(a, b)]
                    for a, b in zip(encontrado, encontrado[1:])
                    if (a, b) in frases
                ],
            }
    return None


def _custo(tipos: Sequence[str], passos: int, cr: float) -> Tuple[int, int, int, int, float]:
    """
    Ordena revisões da menos invasiva para a mais invasiva.

    Inversões primeiro (a pior coisa a propor), depois preferências criadas ou
    abandonadas, depois o tamanho da correção. Trocar a dimensão priorizada por
    ser "um passo mais perto" seria induzir preferência.
    """
    return (
        tipos.count("inversion"),
        tipos.count("preference"),
        sum(1 for t in tipos if t != "unchanged"),
        passos,
        cr,
    )


def suggest_minimal_revision(ahp_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Caminhos de revisão que trazem o CR para dentro do limite.

    Devolve `None` quando não há o que sugerir: matriz já consistente, julgamentos
    de menos, pares demais para enumerar ou nenhuma combinação da escala verbal
    passando no limite.
    """
    judgments: Dict[str, Dict[str, Any]] = ahp_result.get("judgments") or {}
    criteria: List[str] = list(ahp_result.get("criteria_order") or [])
    limite = ahp_result.get("consistency_threshold")
    metodo = ahp_result.get("weight_method") or "column_mean"
    if not judgments or not criteria or limite is None:
        return None
    if len(judgments) > MAX_PARES_ENUMERAVEIS:
        return None

    from domain.methodology import get_methodology

    random_index_table = dict(get_methodology().ahp_random_index)

    chaves = list(judgments.keys())
    eixos: List[List[Tuple[Optional[str], Optional[str]]]] = []
    atuais: List[int] = []
    lados: List[Tuple[str, str]] = []

    for chave in chaves:
        left, _, right = chave.partition("|")
        if not left or not right:
            return None
        eixo = _eixo(left, right)
        indice = _indice_atual(eixo, judgments[chave], left, right)
        if indice is None:
            return None
        eixos.append(eixo)
        atuais.append(indice)
        lados.append((left, right))

    def cr_de(indices: Sequence[int]) -> float:
        hipotese = {
            chave: {
                "ratio": pairwise.matrix_value(
                    lados[n][0], lados[n][1], *eixos[n][indices[n]]
                )
            }
            for n, chave in enumerate(chaves)
        }
        _, matriz, _ = judgments_to_pairwise_matrix(hipotese, criteria)
        return float(
            priority_vector(matriz, method=metodo, random_index_table=random_index_table)[3]
        )

    def avalia(candidato: Sequence[int]) -> Optional[Tuple[Tuple[Any, ...], Dict[str, Any]]]:
        cr = cr_de(candidato)
        if cr > limite:
            return None
        tipos = [_tipo_de_mudanca(a, b) for a, b in zip(atuais, candidato)]
        if all(t == "unchanged" for t in tipos):
            return None
        passos = sum(abs(a - b) for a, b in zip(candidato, atuais))
        return _custo(tipos, passos, cr), _monta_opcao(
            candidato, tipos, cr, chaves, lados, eixos, atuais, judgments
        )

    # Uma opção por comparação: a menor revisão que mexe só naquela pergunta. É o
    # que permite ao gestor escolher de qual julgamento abre mão.
    opcoes: List[Tuple[Tuple[Any, ...], Dict[str, Any], Tuple[int, ...]]] = []
    for i in range(len(chaves)):
        melhor_i: Optional[Tuple[Tuple[Any, ...], Dict[str, Any], Tuple[int, ...]]] = None
        for novo in range(len(eixos[i])):
            if novo == atuais[i]:
                continue
            candidato = list(atuais)
            candidato[i] = novo
            avaliado = avalia(candidato)
            if avaliado and (melhor_i is None or avaliado[0] < melhor_i[0]):
                melhor_i = (*avaliado, tuple(candidato))
        if melhor_i:
            opcoes.append(melhor_i)

    # Garantia de que a primeira opção nunca inverte uma preferência declarada.
    #
    # Consertar uma comparação só nem sempre é possível sem inverter — e, quando
    # não é, a revisão que preserva todas as direções mexe em duas. Ela precisa
    # entrar na lista mesmo custando uma comparação a mais: encabeçar a tela com
    # uma inversão faria o sistema propor ao gestor o contrário do que ele
    # declarou. A varredura das 729 combinações mostra que essa revisão sempre
    # existe (ver testes), então a busca abaixo não volta vazia na prática.
    if not opcoes or all(opcao["inverts_preference"] for _, opcao, _ in opcoes):
        melhor_preservando: Optional[
            Tuple[Tuple[Any, ...], Dict[str, Any], Tuple[int, ...]]
        ] = None
        for candidato in product(*(range(len(e)) for e in eixos)):
            avaliado = avalia(candidato)
            if avaliado is None or avaliado[1]["inverts_preference"]:
                continue
            if melhor_preservando is None or avaliado[0] < melhor_preservando[0]:
                melhor_preservando = (*avaliado, tuple(candidato))
        if melhor_preservando:
            opcoes.append(melhor_preservando)

    # Último recurso: nem conserto isolado, nem revisão sem inversão. Vale a menor
    # revisão que exista — devidamente rotulada como inversão na interface.
    if not opcoes:
        melhor: Optional[Tuple[Tuple[Any, ...], Dict[str, Any], Tuple[int, ...]]] = None
        for candidato in product(*(range(len(e)) for e in eixos)):
            avaliado = avalia(candidato)
            if avaliado and (melhor is None or avaliado[0] < melhor[0]):
                melhor = (*avaliado, tuple(candidato))
        if melhor:
            opcoes.append(melhor)

    if not opcoes:
        return None

    opcoes.sort(key=lambda o: o[0])
    vistos: set = set()
    unicas: List[Dict[str, Any]] = []
    for _custo_da_opcao, opcao, candidato in opcoes:
        if candidato in vistos:
            continue
        vistos.add(candidato)
        unicas.append(opcao)

    return {
        "cycle": _ciclo_de_preferencias(chaves, lados, eixos, atuais),
        "options": unicas[:MAX_OPCOES],
    }


def _monta_opcao(
    candidato: Sequence[int],
    tipos: Sequence[str],
    cr: float,
    chaves: Sequence[str],
    lados: Sequence[Tuple[str, str]],
    eixos: Sequence[Sequence[Tuple[Optional[str], Optional[str]]]],
    atuais: Sequence[int],
    judgments: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """Uma revisão completa, descrita em frases — o que sai e o que entra."""
    mudancas: List[Dict[str, Any]] = []
    for n, chave in enumerate(chaves):
        if candidato[n] == atuais[n]:
            continue
        left, right = lados[n]
        de_pref, de_int = eixos[n][atuais[n]]
        para_pref, para_int = eixos[n][candidato[n]]
        mudancas.append(
            {
                "pair": chave,
                "question_id": judgments[chave].get("question_id"),
                "left": left,
                "right": right,
                "from": {
                    "preference": de_pref,
                    "intensity": de_int,
                    "description": pairwise.describe(left, right, de_pref, de_int),
                },
                "to": {
                    "preference": para_pref,
                    "intensity": para_int,
                    "description": pairwise.describe(left, right, para_pref, para_int),
                },
                "steps": abs(candidato[n] - atuais[n]),
                "kind": tipos[n],
            }
        )

    return {
        "consistency_ratio": round(cr, 4),
        "changed_comparisons": len(mudancas),
        "inverts_preference": "inversion" in tipos,
        "changes": mudancas,
    }
