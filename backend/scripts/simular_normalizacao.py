#!/usr/bin/env python3
"""
Mede o efeito das alternativas de normalização e de exclusão sobre a margem
entre os provedores, nas avaliações já gravadas.

**Por que existe.** O ranking empata com frequência, e "empata" pode ter causas
muito diferentes: evidência que de fato não separa, indicador constante ocupando
peso, ou uma escolha de normalização que comprime as distâncias. Cada causa pede
uma correção distinta, e algumas "correções" fabricam separação em vez de
revelá-la. Este script põe as candidatas lado a lado com o mesmo critério.

**As variantes.**

  base  o que está gravado. ATENÇÃO: os relatórios do acervo foram calculados
        ANTES da regra `comparability.exclude_non_discriminative`, então a base
        é sempre "regra desligada" — é o que torna a comparação abaixo legível.

  sem-constantes   retira da soma o indicador cujo valor normalizado é o mesmo
        para todas as alternativas. É a regra hoje implementada (§9.3
        generalizada). Um indicador assim soma a mesma parcela a todas as
        pontuações: a pontuação sem ele é transformação afim da pontuação com
        ele, e por isso a ORDEM não muda — só a margem, que deixa de ser
        comprimida. A coluna "ordem muda" existe para verificar essa previsão.

  min-max   troca a razão (x/max, min/x) por (x−min)/(max−min) nos indicadores
        quantitativos. NÃO foi adotada, e a coluna "ordem muda" diz por quê: ela
        força o pior provedor a 0 e o melhor a 1 em CADA indicador, de modo que
        uma diferença de 99,9 para 99,99 na disponibilidade vira 0 contra 1. O
        ranking passa a ser artefato da escolha de normalização. A variante fica
        aqui para que a recusa seja documentada com número, não com opinião.

  combinada   as duas juntas, pelo mesmo motivo.

O modo absoluto das rubricas (§10.1) é preservado em todas as variantes: o
Quadro 23 já entrega valor em escala fixa de 0 a 1, e dividir de novo converteria
escala absoluta em relativa — o defeito que o `scales.json` documenta ter medido.

**O que o script NÃO faz.** Não altera nada, não recalcula evidência e não
reextrai. Lê a memória de cálculo gravada e refaz a soma. Para diagnosticar UMA
avaliação (poder de discriminação indicador a indicador, saturação da rubrica),
use `diagnostico_ranking.py`.

    python scripts/simular_normalizacao.py
    python scripts/simular_normalizacao.py --limite 60
    python scripts/simular_normalizacao.py --db /caminho/audit.db
"""

import argparse
import json
import statistics
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from diagnostico_ranking import abrir_db

TOLERANCIA = 0.02
ERRO_MAXIMO = 1e-5
# Abaixo disto dois valores normalizados são o mesmo valor: resíduo de ponto
# flutuante de uma divisão não é diferença. Mesma constante do domínio.
MESMO_VALOR = 1e-9


def extrair(resposta: Mapping[str, Any]) -> Optional[Tuple]:
    """(ids, válidos, pesos efetivos, linhas por (provedor, indicador), gravado)."""
    sintese = resposta.get("synthesis")
    if not sintese:
        return None
    provedores = sintese.get("providers") or []
    if len(provedores) < 2:
        return None
    validos = list(sintese.get("valid_indicators") or [])
    if not validos:
        return None

    pesos = {j: sintese.get("effective_weights", {}).get(j) for j in validos}
    if any(p is None for p in pesos.values()):
        return None

    linhas: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for provedor in provedores:
        for linha in provedor.get("indicators", []):
            linhas[(provedor["id"], linha["indicator_id"])] = linha

    ids = [p["id"] for p in provedores]
    if any((i, j) not in linhas for i in ids for j in validos):
        return None
    if any(linhas[(i, j)].get("normalized_value") is None for i in ids for j in validos):
        return None
    return ids, validos, pesos, linhas, {p["id"]: p["score"] for p in provedores}


def minmax(valores: Mapping[str, float], direcao: Optional[str]) -> Dict[str, float]:
    menor, maior = min(valores.values()), max(valores.values())
    if maior - menor <= MESMO_VALOR:
        # Sem dispersão não há pior: zerar alguém aqui seria inventar diferença.
        return {k: 1.0 for k in valores}
    if direcao == "minimize":
        return {k: (maior - v) / (maior - menor) for k, v in valores.items()}
    return {k: (v - menor) / (maior - menor) for k, v in valores.items()}


def constantes(validos, normalizados, ids) -> List[str]:
    """Indicadores cujo valor normalizado é o mesmo para todas as alternativas."""
    iguais = []
    for j in validos:
        serie = [normalizados[(i, j)] for i in ids]
        if max(serie) - min(serie) <= MESMO_VALOR:
            iguais.append(j)
    return iguais


def pontuar(pesos, normalizados, ids: Sequence[str]) -> Dict[str, float]:
    total = sum(pesos.values())
    if total <= 0:
        return {i: 0.0 for i in ids}
    return {i: sum((p / total) * normalizados[(i, j)] for j, p in pesos.items()) for i in ids}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--db", help="caminho do audit.db")
    parser.add_argument(
        "--limite", type=int, default=200, help="quantas avaliações ler (padrão: 200)"
    )
    args = parser.parse_args()

    conn = abrir_db(args.db)
    linhas_db = conn.execute(
        "select response_json from submissions order by created_at desc limit ?",
        (args.limite,),
    ).fetchall()

    nomes = ["base", "sem-constantes", "min-max", "combinada"]
    margens: Dict[str, List[float]] = {n: [] for n in nomes}
    ordens: Dict[str, List[List[str]]] = {n: [] for n in nomes}
    proporcao_constante: List[float] = []
    usados = 0

    for (bruto,) in linhas_db:
        dados = extrair(json.loads(bruto))
        if dados is None:
            continue
        ids, validos, pesos, linhas, gravado = dados

        r_base = {(i, j): linhas[(i, j)]["normalized_value"] for i in ids for j in validos}
        if max(abs(pontuar(pesos, r_base, ids)[i] - gravado[i]) for i in ids) > ERRO_MAXIMO:
            continue
        usados += 1

        # min-max apenas nos quantitativos; rubrica segue em escala absoluta.
        r_mm = dict(r_base)
        for j in validos:
            meta = linhas[(ids[0], j)]
            if meta.get("data_type") != "quantitative":
                continue
            valores = {i: linhas[(i, j)].get("original_value") for i in ids}
            if any(v is None for v in valores.values()):
                continue
            novo = minmax(valores, meta.get("direction"))
            for i in ids:
                r_mm[(i, j)] = novo[i]

        const_base = set(constantes(validos, r_base, ids))
        const_mm = set(constantes(validos, r_mm, ids))
        peso_total = sum(pesos.values())
        if peso_total > 0:
            proporcao_constante.append(
                sum(pesos[j] for j in const_base) / peso_total
            )

        # A salvaguarda do domínio: se NADA discrimina, não se retira nada.
        def sobrando(const: set) -> Mapping[str, float]:
            restantes = {j: pesos[j] for j in validos if j not in const}
            return restantes or pesos

        variantes = {
            "base": (pesos, r_base),
            "sem-constantes": (sobrando(const_base), r_base),
            "min-max": (pesos, r_mm),
            "combinada": (sobrando(const_mm), r_mm),
        }
        for nome, (p, r) in variantes.items():
            scores = pontuar(p, r, ids)
            ordenados = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
            margens[nome].append(ordenados[0][1] - ordenados[1][1])
            ordens[nome].append([i for i, _ in ordenados])

    if not usados:
        print("Nenhuma avaliação com memória de cálculo suficiente para comparar.")
        return 1

    print(f"Avaliações reconstruídas com fidelidade: {usados} de {len(linhas_db)} lidas")
    if proporcao_constante:
        print(
            "Peso efetivo preso em indicadores constantes (base): "
            f"mediana {statistics.median(proporcao_constante):.1%} | "
            f"máx {max(proporcao_constante):.1%}\n"
        )

    print(f"{'variante':16s} {'margem med.':>12s} {'margem méd.':>12s} {'empates':>13s} {'ordem muda':>13s}")
    for nome in nomes:
        m = margens[nome]
        emp = sum(1 for x in m if x <= TOLERANCIA)
        troca = sum(1 for a, b in zip(ordens["base"], ordens[nome]) if a != b)
        print(
            f"{nome:16s} {statistics.median(m):12.4f} {statistics.mean(m):12.4f} "
            f"{emp:6d} ({emp/usados:3.0%}) {troca:6d} ({troca/usados:3.0%})"
        )

    print(
        "\n'ordem muda' é a coluna decisiva: uma variante que reduz empates trocando\n"
        "a ordem não revelou diferença, produziu outra — e precisa de justificativa\n"
        "metodológica própria, não do fato de empatar menos."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
