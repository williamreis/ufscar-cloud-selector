#!/usr/bin/env python3
"""
Reexecuta o mesmo questionário N vezes e compara o que voltou.

**O que está sendo verificado.** A §5.3 afirma que os procedimentos
quantitativos são reprodutíveis, e a §5.5 apresenta o resultado como auditável.
Duas execuções do mesmo envio, sobre o mesmo índice, precisam então produzir o
mesmo ranking — e não só o mesmo ranking: a mesma leitura de cada documento, ou
a igualdade seria coincidência de arredondamento.

**Por que a afirmação não é óbvia.** A temperatura está em 0, mas isso reduz e
não elimina o não-determinismo de um modelo servido por API. Antes do cache de
extração, 4 das 39 células (provedor × indicador) oscilavam entre `FOUND`,
`PARTIAL` e `NOT_FOUND` em cinco execuções; como a §11.1 exige evidência válida
para todos os provedores na mesma execução, cada oscilação derrubava o indicador
inteiro. O cache existe para fechar isso. Este script é o que mede se fechou.

**O que ele compara**, do mais grosso ao mais fino:

  - a ordem do ranking e a posição de cada provedor;
  - a pontuação de cada provedor, com 6 casas;
  - célula a célula: estado da evidência, categoria da rubrica, valor extraído e
    valor normalizado.

A comparação célula a célula é o que importa. Um ranking estável com células
oscilando é estabilidade por sorte: basta a oscilação cair num indicador de peso
maior para a ordem mudar.

    python scripts/checar_determinismo.py --repeticoes 5
    python scripts/checar_determinismo.py --submissao 3ca6fcff --repeticoes 3
    python scripts/checar_determinismo.py --api http://localhost:8000 --arquivo envio.json

Sai com código 1 quando encontra divergência, para servir de verificação
automatizada.

**Efeito colateral, declarado.** Cada repetição é uma avaliação de verdade: passa
pela API, gasta chamada de LLM quando não há cache e **grava um envio no banco de
auditoria**. Rodar com 5 repetições deixa 5 envios a mais no histórico, com o
mesmo respondente do envio original.
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_BACKEND_ROOT = Path(__file__).resolve().parents[1]

# Chave de uma célula da matriz de desempenho.
Celula = Tuple[str, str]


def _candidatos_db() -> List[Path]:
    raiz = _BACKEND_ROOT.parent
    candidatos = []
    if os.getenv("AUDIT_DB_PATH"):
        candidatos.append(Path(os.environ["AUDIT_DB_PATH"]))
    candidatos += [_BACKEND_ROOT / "data" / "audit.db", raiz / "audit.db"]
    return candidatos


def carregar_envio(submissao: Optional[str], db: Optional[str]) -> Tuple[str, Dict[str, Any]]:
    """Payload original de um envio já gravado — o mesmo que a API recebeu."""
    tentativas = [Path(db)] if db else _candidatos_db()
    caminho = next((t for t in tentativas if t.exists()), None)
    if caminho is None:
        raise SystemExit(
            "Banco de auditoria não encontrado. Procurei em:\n  "
            + "\n  ".join(str(t) for t in tentativas)
        )

    with sqlite3.connect(f"file:{caminho}?mode=ro", uri=True) as conn:
        sql = "select id, request_json from submissions"
        params: Tuple[Any, ...] = ()
        if submissao:
            sql += " where id like ?"
            params = (f"{submissao}%",)
        sql += " order by created_at desc limit 1"
        linha = conn.execute(sql, params).fetchone()

    if linha is None:
        raise SystemExit("Nenhum envio encontrado para reexecutar.")
    return linha[0], json.loads(linha[1])


def executar(api: str, payload: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    """Uma execução contra a API. Erro de rede interrompe: medir metade não mede."""
    requisicao = urllib.request.Request(
        url=f"{api.rstrip('/')}/api/recommend",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
            return json.loads(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detalhe = exc.read().decode("utf-8", errors="replace")[:400]
        raise SystemExit(f"A API respondeu {exc.code}: {detalhe}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"Não foi possível falar com a API em {api}: {exc.reason}. "
            "Suba a aplicação (`make up`) antes de medir."
        ) from exc


# ---------------------------------------------------------------------------
# Impressões comparáveis
# ---------------------------------------------------------------------------


def impressao_ranking(resposta: Dict[str, Any]) -> List[Tuple[str, int, float]]:
    return [
        (str(r["id"]), int(r["rank"]), round(float(r["score"]), 6))
        for r in resposta.get("ranking", [])
    ]


def impressao_celulas(resposta: Dict[str, Any]) -> Dict[Celula, Tuple[Any, ...]]:
    """
    `(provedor, indicador) → (estado, categoria, valor extraído, normalizado)`.

    É a leitura do documento, antes de virar pontuação. Comparar aqui é o que
    distingue "o resultado é estável" de "o resultado é o mesmo por acaso".
    """
    celulas: Dict[Celula, Tuple[Any, ...]] = {}
    for provedor in (resposta.get("synthesis") or {}).get("providers", []):
        for linha in provedor.get("indicators", []):
            celulas[(provedor["id"], linha["indicator_id"])] = (
                linha.get("status"),
                linha.get("category"),
                linha.get("original_value"),
                linha.get("normalized_value"),
            )
    return celulas


def comparar(execucoes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Divergências da 2ª execução em diante contra a primeira."""
    referencia, *demais = execucoes
    ranking_ref = impressao_ranking(referencia)
    celulas_ref = impressao_celulas(referencia)

    rankings_divergentes: List[int] = []
    celulas_instaveis: Dict[Celula, List[Tuple[int, Tuple[Any, ...]]]] = {}

    for i, execucao in enumerate(demais, start=2):
        if impressao_ranking(execucao) != ranking_ref:
            rankings_divergentes.append(i)
        celulas = impressao_celulas(execucao)
        for chave in set(celulas_ref) | set(celulas):
            atual, esperado = celulas.get(chave), celulas_ref.get(chave)
            if atual != esperado:
                celulas_instaveis.setdefault(chave, []).append((i, atual))

    return {
        "execucoes": len(execucoes),
        "ranking_referencia": ranking_ref,
        "rankings_divergentes": rankings_divergentes,
        "celulas_referencia": celulas_ref,
        "celulas_instaveis": celulas_instaveis,
        "total_celulas": len(celulas_ref),
    }


def relatorio(envio_id: str, resultado: Dict[str, Any]) -> List[str]:
    n = resultado["execucoes"]
    instaveis = resultado["celulas_instaveis"]
    total = resultado["total_celulas"]

    linhas = [
        f"## Determinismo — envio `{envio_id[:12]}` reexecutado {n}×",
        "",
        "### Ranking",
        "",
        "| Posição | Provedor | Pontuação |",
        "| --- | --- | ---: |",
    ]
    for pid, posicao, score in resultado["ranking_referencia"]:
        linhas.append(f"| {posicao}º | {pid} | {score:.6f} |")

    divergentes = resultado["rankings_divergentes"]
    linhas += [
        "",
        (
            f"- **{len(divergentes)} de {n - 1}** reexecuções produziram ranking diferente "
            f"(execuções {divergentes})."
            if divergentes
            else f"- Todas as {n - 1} reexecuções reproduziram o mesmo ranking."
        ),
        "",
        "### Células (provedor × indicador)",
        "",
        f"- {total} células comparadas por execução.",
        (
            f"- **{len(instaveis)} {'oscilou' if len(instaveis) == 1 else 'oscilaram'}** "
            f"({100.0 * len(instaveis) / total:.1f}%)."
            if instaveis and total
            else "- Nenhuma célula oscilou: a leitura de cada documento se repetiu por inteiro."
        ),
        "",
    ]

    if instaveis:
        linhas += [
            "| Provedor | Indicador | Referência (execução 1) | Divergências |",
            "| --- | --- | --- | --- |",
        ]
        for (provedor, indicador), ocorrencias in sorted(instaveis.items()):
            ref = resultado["celulas_referencia"].get((provedor, indicador))
            vistos = "; ".join(f"exec {i}: {valor}" for i, valor in ocorrencias)
            linhas.append(f"| {provedor} | {indicador} | {ref} | {vistos} |")
        linhas.append("")

    return linhas


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica se o mesmo envio produz o mesmo resultado.")
    parser.add_argument("--api", default="http://localhost:8000", help="base da API")
    parser.add_argument("--repeticoes", type=int, default=3, help="execuções (mínimo 2)")
    parser.add_argument("--submissao", help="envio a reexecutar (padrão: o mais recente)")
    parser.add_argument("--arquivo", help="payload em JSON, em vez de buscar no banco")
    parser.add_argument("--db", help="caminho do audit.db")
    parser.add_argument("--timeout", type=int, default=600, help="timeout de cada execução, em segundos")
    args = parser.parse_args()

    if args.repeticoes < 2:
        raise SystemExit("São necessárias ao menos 2 execuções para haver o que comparar.")

    if args.arquivo:
        envio_id, payload = Path(args.arquivo).stem, json.loads(
            Path(args.arquivo).read_text(encoding="utf-8")
        )
    else:
        envio_id, payload = carregar_envio(args.submissao, args.db)

    execucoes = []
    for i in range(1, args.repeticoes + 1):
        print(f"execução {i}/{args.repeticoes}…", file=sys.stderr)
        execucoes.append(executar(args.api, payload, args.timeout))

    resultado = comparar(execucoes)
    print("\n".join(relatorio(envio_id, resultado)))

    estavel = not resultado["rankings_divergentes"] and not resultado["celulas_instaveis"]
    return 0 if estavel else 1


if __name__ == "__main__":
    raise SystemExit(main())
