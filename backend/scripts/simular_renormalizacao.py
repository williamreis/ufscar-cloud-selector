#!/usr/bin/env python3
"""
Compara as duas formas possíveis de renormalizar o peso (§11.2), sobre as
avaliações já gravadas.

**A pergunta.** Quando um indicador sai do conjunto comparável, o peso dele
precisa ir para algum lugar. Há duas respostas defensáveis:

  A (implementada)  w'_j = w_j / Σ_{k∈V} w_k
      o peso vai para TODOS os sobreviventes, de todas as dimensões.

  B (alternativa)   w'_j = W_d × w_j / Σ_{k∈V∩d} w_k
      o peso fica dentro da dimensão; o `W_d` que o AHP atribuiu é preservado.
      Dimensão que perde todos os seus indicadores não tem onde colocar o peso —
      só nesse caso ele vai para as demais, proporcional a `W_d`.

**Por que a pergunta importa.** Sob A, o peso migra sistematicamente das
dimensões cujos indicadores falham para as que sobrevivem. Medido no acervo:
Segurança, cujos quatro indicadores são qualitativos e quase nunca saem, recebeu
uma mediana de +5,3 pontos percentuais que o gestor não lhe deu, enquanto
Sustentabilidade e Desempenho perderam. A prioridade declarada deriva para a
dimensão que melhor se documenta.

Não é defeito de implementação: é consequência de A, e A tem defesa ("só se pode
ponderar o que se consegue medir"). Mas é uma decisão acadêmica que precisa ser
tomada com o efeito à vista, e não por omissão.

**O que o script NÃO faz.** Não altera nada e não recalcula evidência: lê o
`response_json` gravado, refaz a Equação 5 sob as duas políticas e compara. A
política A é reconstruída primeiro e conferida contra o score gravado; avaliação
que não bate é descartada em vez de entrar no resumo com número aproximado.

    python scripts/simular_renormalizacao.py
    python scripts/simular_renormalizacao.py --limite 60
    python scripts/simular_renormalizacao.py --db /caminho/audit.db
"""

import argparse
import collections
import json
import statistics
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from diagnostico_ranking import abrir_db

# Margem de indiferença do scales.json. Fica aqui como constante do relato: o
# script compara políticas, não decide empate.
TOLERANCIA = 0.02
# Acima disto a reconstrução da política A não reproduz o score gravado, e a
# avaliação não serve de base de comparação.
ERRO_MAXIMO = 1e-5


def extrair(resposta: Mapping[str, Any]) -> Optional[Tuple]:
    """
    (pesos globais, dimensão por indicador, r_ij, W_d, válidos, ids, gravado)

    `None` quando falta dado para refazer a conta — envios anteriores à memória
    de cálculo por indicador, por exemplo.
    """
    sintese = resposta.get("synthesis")
    pesos_raw = (resposta.get("indicator_weights") or {}).get("indicators")
    ahp = resposta.get("criteria_weights")
    if not sintese or not pesos_raw or not ahp:
        return None
    provedores = sintese.get("providers") or []
    if len(provedores) < 2:
        return None

    globais = {i["indicator_id"]: i["global_weight"] for i in pesos_raw if i.get("global_weight")}
    dimensao = {i["indicator_id"]: i["dimension"] for i in pesos_raw}
    validos = [i["indicator_id"] for i in pesos_raw if i.get("effective_weight")]
    if not validos:
        return None

    normalizados: Dict[Tuple[str, str], float] = {}
    for provedor in provedores:
        for linha in provedor.get("indicators", []):
            if linha.get("in_comparison") and linha.get("normalized_value") is not None:
                normalizados[(provedor["id"], linha["indicator_id"])] = linha["normalized_value"]

    ids = [p["id"] for p in provedores]
    if any((pid, j) not in normalizados for pid in ids for j in validos):
        return None
    gravado = {p["id"]: p["score"] for p in provedores}
    return globais, dimensao, normalizados, ahp, validos, ids, gravado


def pesos_globais(globais, validos, *_ignorado) -> Dict[str, float]:
    """Política A: renormaliza sobre todo o conjunto comparável."""
    total = sum(globais[j] for j in validos)
    return {j: globais[j] / total for j in validos} if total > 0 else {}


def pesos_por_dimensao(globais, validos, dimensao, ahp) -> Dict[str, float]:
    """Política B: o peso fica na dimensão; `W_d` do AHP é preservado."""
    por_dimensao: Dict[str, List[str]] = collections.defaultdict(list)
    for j in validos:
        por_dimensao[dimensao[j]].append(j)

    vivas = {d: ahp.get(d, 0.0) for d in por_dimensao}
    soma_vivas = sum(vivas.values())
    if soma_vivas <= 0:
        return pesos_globais(globais, validos)

    # Só as dimensões que sobraram dividem o total; a que perdeu todos os
    # indicadores não tem como manter o peso dela.
    escala = 1.0 / soma_vivas
    pesos: Dict[str, float] = {}
    for d, indicadores in por_dimensao.items():
        total_d = sum(globais[j] for j in indicadores)
        if total_d <= 0:
            continue
        for j in indicadores:
            pesos[j] = ahp.get(d, 0.0) * escala * (globais[j] / total_d)
    return pesos


def pontuar(pesos: Mapping[str, float], normalizados, ids: Sequence[str]) -> Dict[str, float]:
    total = sum(pesos.values())
    if total <= 0:
        return {i: 0.0 for i in ids}
    return {
        i: sum((p / total) * normalizados[(i, j)] for j, p in pesos.items()) for i in ids
    }


def ordenar(scores: Mapping[str, float]) -> List[Tuple[str, float]]:
    # Desempate por id só para a saída ser estável entre execuções.
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--db", help="caminho do audit.db")
    parser.add_argument(
        "--limite", type=int, default=200, help="quantas avaliações ler (padrão: 200)"
    )
    args = parser.parse_args()

    conn = abrir_db(args.db)
    linhas = conn.execute(
        "select id, created_at, response_json from submissions order by created_at desc limit ?",
        (args.limite,),
    ).fetchall()

    usados = 0
    troca_ordem = 0
    troca_lider = 0
    troca_decidida = 0
    margens: Dict[str, List[float]] = {"A": [], "B": []}
    empates = {"A": 0, "B": 0}
    deriva: Dict[str, List[Tuple[float, float]]] = collections.defaultdict(list)
    exemplos: List[Tuple[str, str, str, str, float, float]] = []

    for sid, criado, bruto in linhas:
        dados = extrair(json.loads(bruto))
        if dados is None:
            continue
        globais, dimensao, normalizados, ahp, validos, ids, gravado = dados

        pesos = {
            "A": pesos_globais(globais, validos),
            "B": pesos_por_dimensao(globais, validos, dimensao, ahp),
        }
        scores = {k: pontuar(v, normalizados, ids) for k, v in pesos.items()}

        # Fidelidade: sem reproduzir o score gravado, a comparação não vale.
        if max(abs(scores["A"][i] - gravado[i]) for i in ids) > ERRO_MAXIMO:
            continue
        usados += 1

        ordem = {k: ordenar(v) for k, v in scores.items()}
        for k in ("A", "B"):
            margem = ordem[k][0][1] - ordem[k][1][1]
            margens[k].append(margem)
            empates[k] += margem <= TOLERANCIA

        if [i for i, _ in ordem["A"]] != [i for i, _ in ordem["B"]]:
            troca_ordem += 1
            if ordem["A"][0][0] != ordem["B"][0][0]:
                troca_lider += 1
                margem_a = ordem["A"][0][1] - ordem["A"][1][1]
                if margem_a > TOLERANCIA:
                    # O relatório APRESENTAVA um vencedor: a troca é visível.
                    troca_decidida += 1
                exemplos.append(
                    (
                        str(criado)[:10], sid[:8],
                        ordem["A"][0][0], ordem["B"][0][0],
                        margem_a, ordem["B"][0][1] - ordem["B"][1][1],
                    )
                )

        for d in ahp:
            soma = {
                k: sum(p / sum(pesos[k].values()) for j, p in pesos[k].items() if dimensao[j] == d)
                for k in ("A", "B")
            }
            deriva[d].append((soma["A"] - ahp[d], soma["B"] - ahp[d]))

    if not usados:
        print("Nenhuma avaliação com memória de cálculo suficiente para comparar.")
        return 1

    print(f"Avaliações reconstruídas com fidelidade: {usados} de {len(linhas)} lidas\n")

    print("1) A ordem do ranking muda?")
    print(f"   ordem diferente ........ {troca_ordem} ({troca_ordem / usados:.0%})")
    print(f"   líder diferente ........ {troca_lider} ({troca_lider / usados:.0%})")
    print(f"   líder diferente num caso JÁ DECIDIDO (margem > {TOLERANCIA}) ... {troca_decidida}")

    print("\n2) Margem entre 1º e 2º:")
    for chave, rotulo in (("A", "A global (atual)"), ("B", "B por dimensão")):
        m = margens[chave]
        print(
            f"   {rotulo:20s} mediana {statistics.median(m):.4f} | "
            f"média {statistics.mean(m):.4f} | empates {empates[chave]} ({empates[chave]/usados:.0%})"
        )

    print("\n3) Deriva do peso da dimensão em relação ao AHP (pontos percentuais):")
    print(f"   {'dimensão':18s} {'A mediana':>10s} {'A pior':>9s} {'B mediana':>10s} {'B pior':>9s}")
    for d in sorted(deriva):
        a = [x[0] for x in deriva[d]]
        b = [x[1] for x in deriva[d]]
        print(
            f"   {d:18s} {statistics.median(a)*100:+9.2f} {max(a, key=abs)*100:+8.2f} "
            f"{statistics.median(b)*100:+9.2f} {max(b, key=abs)*100:+8.2f}"
        )

    if exemplos:
        print("\n4) Avaliações em que o líder muda:")
        print(f"   {'data':11s} {'id':9s} {'A':>7s} {'B':>7s} {'margem A':>10s} {'margem B':>10s}")
        for criado, sid, lider_a, lider_b, margem_a, margem_b in exemplos:
            print(f"   {criado:11s} {sid:9s} {lider_a:>7s} {lider_b:>7s} {margem_a:+10.4f} {margem_b:+10.4f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
