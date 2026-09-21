#!/usr/bin/env python3
"""
Mede de onde vem — e de onde não vem — a diferença entre os provedores.

Existe por causa de uma observação feita na defesa: as pontuações do ranking se
separam por centésimos. A pergunta "por quê" não se responde olhando o ranking,
porque a pontuação é uma soma de treze parcelas e o que importa é **quais delas
variam entre os provedores**. Um indicador em que os três recebem o mesmo valor
normalizado soma a mesma constante a todos: ele entra no cálculo, ocupa peso, e
não separa ninguém. O ranking acaba decidido por um subconjunto pequeno, e o
relatório não diz qual é.

O que este script calcula, a partir da memória de cálculo já persistida:

  - **amplitude observada** — distância entre o 1º e o último colocado, e entre
    o 1º e o 2º, comparadas à margem de indiferença do `scales.json`;

  - **poder de discriminação de cada indicador** — `w'_j × (max_i r_ij − min_i r_ij)`.
    É o quanto aquele indicador, sozinho, pode afastar dois provedores. Zero
    significa que ele não participa da ordenação, por mais que pese;

  - **amplitude potencial** — a soma desses valores. É o teto da separação que o
    conjunto comparável consegue produzir. Quando ela já é pequena, nenhuma
    escolha de pesos vai espalhar as pontuações: a evidência é que não distingue;

  - **saturação da rubrica** — quantas células caíram em cada nível do Quadro 23.
    Um nível concentrando quase tudo é a causa mais comum de indicador que não
    discrimina.

Nada aqui recalcula o ranking nem altera nada: lê o que foi gravado e mede.

    python scripts/diagnostico_ranking.py                    # última avaliação
    python scripts/diagnostico_ranking.py --submissao <id>
    python scripts/diagnostico_ranking.py --arquivo resposta.json
    python scripts/diagnostico_ranking.py --todas            # resumo comparativo

**Sobre envios antigos.** Até a Fase 2 a normalização era aditiva (`x/Σx`), que
não é a Equação 1 da dissertação, e o próprio payload se identifica com
`"mode": "distributive"`. Nesses casos o script não finge um diagnóstico: mostra
a compressão que aquele método impunha e o que as mesmas notas dariam pela
Equação 1, para que a diferença entre os dois fique documentada.
"""

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Abaixo disto duas normalizações são o mesmo número: a diferença é ruído de
# arredondamento da serialização (6 casas), não desempenho distinto.
EPS = 1e-6

_BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _metodologia(var: str, arquivo: str) -> Dict[str, Any]:
    """
    Lê `scales.json` / `indicators.json` direto do disco.

    A configuração metodológica é externalizada por decisão da própria pesquisa
    (§45), e é dela que o cálculo lê — então ler o arquivo é ler a fonte, não uma
    cópia. Importar `domain.methodology` traria junto `config`, `dotenv` e o
    resto do backend, e este script precisa rodar fora do contêiner: é ele que
    produz material para o texto, muitas vezes numa máquina sem o ambiente da
    aplicação montado.
    """
    caminho = Path(os.environ[var]) if os.getenv(var) else _BACKEND_ROOT / "methodology" / arquivo
    return json.loads(caminho.read_text(encoding="utf-8"))


DIMENSAO_NOME = {
    "sustainability": "Sustentabilidade",
    "performance": "Desempenho",
    "security": "Segurança",
}

MOTIVO = {
    "no_evidence": "sem evidência em nenhum provedor",
    "missing_for_some_providers": "evidência ausente em algum provedor",
    "non_discriminative": "fórmula indefinida (todos zerados)",
    "invalid_for_comparison": "valores incomparáveis",
    "no_weight": "sem peso (relevância não informada)",
    "vintage_mismatch": "evidências de períodos distantes demais",
}


# ---------------------------------------------------------------------------
# Localizar e carregar a avaliação
# ---------------------------------------------------------------------------


def _candidatos_db() -> List[Path]:
    """
    Caminhos onde o banco de auditoria costuma estar.

    `AUDIT_DB_PATH` é a mesma variável que o backend lê, e vem primeiro. As
    demais cobrem os dois arranjos usuais: o volume do contêiner e a cópia na
    raiz do repositório usada em execução local.
    """
    raiz = Path(__file__).resolve().parents[2]
    candidatos = []
    if os.getenv("AUDIT_DB_PATH"):
        candidatos.append(Path(os.environ["AUDIT_DB_PATH"]))
    candidatos += [raiz / "backend" / "data" / "audit.db", raiz / "audit.db"]
    return candidatos


def abrir_db(caminho: Optional[str]) -> sqlite3.Connection:
    tentativas = [Path(caminho)] if caminho else _candidatos_db()
    for tentativa in tentativas:
        if tentativa.exists():
            return sqlite3.connect(f"file:{tentativa}?mode=ro", uri=True)
    procurados = "\n  ".join(str(t) for t in tentativas)
    raise SystemExit(f"Banco de auditoria não encontrado. Procurei em:\n  {procurados}")


def carregar_envios(
    conn: sqlite3.Connection, submissao: Optional[str]
) -> List[Tuple[str, str, Dict[str, Any]]]:
    """Devolve (id, created_at, response) — a última avaliação, ou a pedida."""
    sql = "select id, created_at, response_json from submissions"
    params: Sequence[Any] = ()
    if submissao:
        # Prefixo basta: o resumo (`--todas`) imprime os ids abreviados, e exigir
        # os 32 caracteres obrigaria a copiar do banco o que a tela já mostrou.
        sql += " where id like ?"
        params = (f"{submissao}%",)
    sql += " order by created_at desc"

    linhas = conn.execute(sql, params).fetchall()
    if not linhas:
        raise SystemExit(
            f"Nenhuma avaliação encontrada{f' com id {submissao}' if submissao else ''}."
        )
    return [(i, criado, json.loads(payload)) for i, criado, payload in linhas]


# ---------------------------------------------------------------------------
# Diagnóstico
# ---------------------------------------------------------------------------


def _ranking(synthesis: Dict[str, Any]) -> List[Dict[str, Any]]:
    return sorted(synthesis.get("providers", []), key=lambda p: -float(p.get("score", 0.0)))


def _matriz_normalizada(
    synthesis: Dict[str, Any],
) -> Dict[str, Dict[str, float]]:
    """
    `indicator_id → {provider_id: valor normalizado}`, só do conjunto comparável.

    Linhas fora da comparação são ignoradas de propósito: elas não entram na
    Equação 5, e incluí-las na medida de discriminação atribuiria poder de
    separação a um indicador que não separa nada porque nem participa.
    """
    validos = set(synthesis.get("valid_indicators", ()))
    matriz: Dict[str, Dict[str, float]] = {}
    for provedor in synthesis.get("providers", []):
        for linha in provedor.get("indicators", []):
            iid = linha.get("indicator_id")
            valor = linha.get("normalized_value")
            if iid in validos and valor is not None:
                matriz.setdefault(iid, {})[provedor["id"]] = float(valor)
    return matriz


def _categorias(synthesis: Dict[str, Any]) -> Dict[str, int]:
    """Quantas células caíram em cada nível do Quadro 23 (saturação da rubrica)."""
    contagem: Dict[str, int] = {}
    for provedor in synthesis.get("providers", []):
        for linha in provedor.get("indicators", []):
            categoria = linha.get("category")
            if categoria:
                contagem[categoria] = contagem.get(categoria, 0) + 1
    return dict(sorted(contagem.items(), key=lambda kv: -kv[1]))


def analisar(resposta: Dict[str, Any]) -> Dict[str, Any]:
    """Todos os números do diagnóstico, sem formatação."""
    synthesis = resposta.get("synthesis") or {}
    escalas = _metodologia("METHODOLOGY_SCALES_PATH", "scales.json")
    tolerancia = float(escalas.get("tie_break", {}).get("tolerance", 0.0))
    total_indicadores = len(_metodologia("INDICATORS_PATH", "indicators.json")["indicators"])
    ranking = _ranking(synthesis)
    matriz = _matriz_normalizada(synthesis)
    pesos = synthesis.get("effective_weights", {})
    tipos = {i["indicator_id"]: i.get("data_type") for i in synthesis.get("indicators", [])}
    nomes = {i["indicator_id"]: i.get("name") for i in synthesis.get("indicators", [])}
    dimensoes = {i["indicator_id"]: i.get("dimension") for i in synthesis.get("indicators", [])}

    detalhe: List[Dict[str, Any]] = []
    for iid, valores in matriz.items():
        if not valores:
            continue
        amplitude = max(valores.values()) - min(valores.values())
        peso = float(pesos.get(iid, 0.0))
        detalhe.append(
            {
                "indicator_id": iid,
                "name": nomes.get(iid, iid),
                "dimension": dimensoes.get(iid, ""),
                "data_type": tipos.get(iid),
                "effective_weight": peso,
                "spread": amplitude,
                # Quanto este indicador sozinho pode afastar dois provedores.
                "discriminacao": peso * amplitude,
                "valores": valores,
            }
        )
    detalhe.sort(key=lambda d: -d["discriminacao"])

    discriminam = [d for d in detalhe if d["spread"] > EPS]
    potencial = sum(d["discriminacao"] for d in detalhe)
    por_tipo = {
        "quantitative": sum(
            d["discriminacao"] for d in detalhe if d["data_type"] == "quantitative"
        ),
        "qualitative": sum(
            d["discriminacao"] for d in detalhe if d["data_type"] == "qualitative"
        ),
    }

    scores = [float(p["score"]) for p in ranking]
    return {
        "mode": synthesis.get("mode"),
        "ranking": ranking,
        "amplitude_observada": (scores[0] - scores[-1]) if len(scores) > 1 else 0.0,
        "gap_1_2": (scores[0] - scores[1]) if len(scores) > 1 else 0.0,
        "tolerancia": tolerancia,
        "validos": list(synthesis.get("valid_indicators", ())),
        "excluidos": dict(synthesis.get("excluded_indicators", {})),
        "total_indicadores": total_indicadores,
        "detalhe": detalhe,
        "discriminam": discriminam,
        "amplitude_potencial": potencial,
        "por_tipo": por_tipo,
        "categorias": _categorias(synthesis),
        "sensitivity": resposta.get("sensitivity"),
    }


# ---------------------------------------------------------------------------
# Envios no formato antigo (normalização aditiva)
# ---------------------------------------------------------------------------


def relatorio_legado(resposta: Dict[str, Any]) -> List[str]:
    """
    O que o método aditivo fazia com as notas, e o que a Equação 1 faria.

    A comparação é feita sobre os mesmos números brutos gravados no envio, para
    que a diferença observada seja só a da regra de normalização.
    """
    synthesis = resposta["synthesis"]
    criterios = synthesis.get("criteria_order", [])
    pesos = synthesis.get("weights", {})
    provedores = synthesis.get("providers", [])

    brutos = {
        p["id"]: {c: float(p["cells"][c]["raw"]) for c in criterios if c in p["cells"]}
        for p in provedores
    }
    nomes = {p["id"]: p["name"] for p in provedores}
    maximos = {c: max(b[c] for b in brutos.values()) for c in criterios}

    aditivo = {p: float(next(x for x in provedores if x["id"] == p)["score"]) for p in brutos}
    equacao1 = {
        p: sum(float(pesos.get(c, 0.0)) * brutos[p][c] / maximos[c] for c in criterios)
        for p in brutos
    }

    def amplitude(d: Dict[str, float]) -> float:
        return max(d.values()) - min(d.values())

    linhas = [
        "> **Envio no formato antigo (`mode: distributive`).** A normalização usada",
        "> foi aditiva (`x/Σx`), que não é a Equação 1 da dissertação. Com ela as",
        "> pontuações somam 1 por construção e ficam presas em torno de 1/n — a",
        "> proximidade é consequência da fórmula, não dos provedores.",
        "",
        "| Provedor | aditivo (`x/Σx`) | Equação 1 (`x/max`) |",
        "| --- | ---: | ---: |",
    ]
    for pid in sorted(aditivo, key=lambda p: -aditivo[p]):
        linhas.append(f"| {nomes[pid]} | {aditivo[pid]:.4f} | {equacao1[pid]:.4f} |")

    amp_a, amp_e = amplitude(aditivo), amplitude(equacao1)
    fator = (amp_e / amp_a) if amp_a > 0 else float("nan")
    linhas += [
        f"| **soma** | **{sum(aditivo.values()):.4f}** | {sum(equacao1.values()):.4f} |",
        f"| **amplitude 1º–último** | **{amp_a:.4f}** | **{amp_e:.4f}** |",
        "",
        f"A Equação 1 expande a amplitude em **{fator:.2f}×** sobre os mesmos dados.",
        "",
        "Refaça a avaliação na versão atual para obter o diagnóstico completo.",
    ]
    return linhas


# ---------------------------------------------------------------------------
# Formatação
# ---------------------------------------------------------------------------


def _pct(valor: float, total: float) -> str:
    return f"{(100.0 * valor / total):.1f}%" if total > 0 else "—"


def relatorio(envio_id: str, criado: str, resposta: Dict[str, Any]) -> List[str]:
    synthesis = resposta.get("synthesis") or {}
    cabecalho = [f"## Avaliação `{envio_id[:12]}` — {criado}", ""]

    if synthesis.get("mode") != "weighted_sum":
        return cabecalho + relatorio_legado(resposta)

    a = analisar(resposta)
    linhas = cabecalho

    # 1. Amplitude
    empate = a["gap_1_2"] <= a["tolerancia"]
    linhas += [
        "### 1. Amplitude do ranking",
        "",
        "| Posição | Provedor | Pontuação |",
        "| --- | --- | ---: |",
    ]
    for p in a["ranking"]:
        marca = " *(empate)*" if p.get("tied") else ""
        linhas.append(f"| {p['rank']}º | {p['name']}{marca} | {float(p['score']):.4f} |")
    linhas += [
        "",
        f"- Distância 1º–último: **{a['amplitude_observada']:.4f}**",
        f"- Distância 1º–2º: **{a['gap_1_2']:.4f}** "
        f"(margem de indiferença: {a['tolerancia']:.2f}) → "
        + ("**empate técnico**" if empate else "diferença acima da margem"),
        "",
    ]

    # 2. Base comparável
    linhas += [
        "### 2. Base comparável",
        "",
        f"- {len(a['validos'])} de {a['total_indicadores']} indicadores entraram na Equação 5.",
        f"- Destes, **{len(a['discriminam'])} produzem alguma diferença** entre os provedores; "
        f"{len(a['validos']) - len(a['discriminam'])} atribuem o mesmo valor a todos e "
        "não participam da ordenação.",
        "",
    ]
    if a["excluidos"]:
        linhas.append("Indicadores fora da comparação:")
        linhas.append("")
        for iid, motivo in sorted(a["excluidos"].items()):
            linhas.append(f"- `{iid}` — {MOTIVO.get(motivo, motivo)}")
        linhas.append("")

    # 3. Poder de discriminação
    linhas += [
        "### 3. Poder de discriminação por indicador",
        "",
        "`discriminação = peso efetivo × (maior − menor valor normalizado)`. É o quanto",
        "o indicador pode, sozinho, afastar dois provedores.",
        "",
        "| Indicador | Dim. | Tipo | Peso efetivo | Amplitude | Discriminação | % do total |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for d in a["detalhe"]:
        tipo = "quant." if d["data_type"] == "quantitative" else "qual."
        linhas.append(
            f"| {d['name']} | {DIMENSAO_NOME.get(d['dimension'], d['dimension'])} | {tipo} "
            f"| {d['effective_weight']:.4f} | {d['spread']:.4f} "
            f"| {d['discriminacao']:.4f} | {_pct(d['discriminacao'], a['amplitude_potencial'])} |"
        )

    potencial = a["amplitude_potencial"]
    linhas += [
        "",
        f"- **Amplitude potencial:** {potencial:.4f} — teto da separação que este conjunto "
        "comparável consegue produzir, com qualquer distribuição de pesos.",
        f"- **Amplitude observada:** {a['amplitude_observada']:.4f} "
        f"({_pct(a['amplitude_observada'], potencial)} do teto).",
        f"- Indicadores quantitativos respondem por {_pct(a['por_tipo']['quantitative'], potencial)} "
        f"da discriminação; qualitativos, por {_pct(a['por_tipo']['qualitative'], potencial)}.",
        "",
    ]

    # 4. Saturação da rubrica
    if a["categorias"]:
        total_celulas = sum(a["categorias"].values())
        linhas += [
            "### 4. Saturação da rubrica (Quadro 23)",
            "",
            "| Nível | Células | % |",
            "| --- | ---: | ---: |",
        ]
        for categoria, n in a["categorias"].items():
            linhas.append(f"| {categoria} | {n} | {_pct(n, total_celulas)} |")
        maior = next(iter(a["categorias"].values()))
        linhas += [
            "",
            f"O nível mais frequente concentra {_pct(maior, total_celulas)} das células. "
            "Quanto maior essa concentração, menos os indicadores qualitativos separam "
            "os provedores — eles somam a mesma constante a todos.",
            "",
        ]

    # 5. Robustez
    s = a["sensitivity"]
    if s:
        linhas += ["### 5. Robustez do 1º lugar", ""]
        if s.get("robust"):
            linhas.append(
                "Nenhum peso de dimensão, sozinho, troca o líder dentro de [0,1]."
            )
        else:
            linhas.append(
                f"Bastaria mover o peso de **{DIMENSAO_NOME.get(s.get('most_fragile_dimension'), s.get('most_fragile_dimension'))}** "
                f"em {abs(float(s.get('most_fragile_delta') or 0.0)) * 100:.1f} pontos percentuais "
                "para o topo mudar."
            )
        linhas.append("")

    return linhas


def resumo_todas(envios: List[Tuple[str, str, Dict[str, Any]]]) -> List[str]:
    """Uma linha por avaliação — serve para ver se a compressão é sistemática."""
    linhas = [
        "## Resumo comparativo das avaliações",
        "",
        "`Discriminam` = indicadores válidos cujo valor normalizado difere entre provedores.",
        "`Nível dominante` = fração das células da rubrica concentradas no nível mais frequente.",
        "",
        "| Avaliação | Data | Método | Válidos | Discriminam | Amplitude | Potencial | Nível dominante |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for envio_id, criado, resposta in envios:
        synthesis = resposta.get("synthesis") or {}
        modo = synthesis.get("mode", "—")
        if modo != "weighted_sum":
            linhas.append(
                f"| `{envio_id[:8]}` | {criado[:10]} | {modo} | — | — | — | — | — |"
            )
            continue
        a = analisar(resposta)
        categorias = a["categorias"]
        if categorias:
            nivel, n = next(iter(categorias.items()))
            dominante = f"{nivel} {_pct(n, sum(categorias.values()))}"
        else:
            dominante = "—"
        linhas.append(
            f"| `{envio_id[:8]}` | {criado[:10]} | {modo} | "
            f"{len(a['validos'])}/{a['total_indicadores']} | {len(a['discriminam'])} | "
            f"{a['amplitude_observada']:.4f} | {a['amplitude_potencial']:.4f} | {dominante} |"
        )
    return linhas + [""]


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnóstico da separação entre provedores.")
    parser.add_argument("--submissao", help="id da avaliação (padrão: a mais recente)")
    parser.add_argument("--arquivo", help="payload de resposta em JSON, em vez do banco")
    parser.add_argument("--db", help="caminho do audit.db")
    parser.add_argument(
        "--todas", action="store_true", help="uma linha por avaliação, em vez do detalhe"
    )
    args = parser.parse_args()

    if args.arquivo:
        resposta = json.loads(Path(args.arquivo).read_text(encoding="utf-8"))
        envios = [(resposta.get("submission_id") or Path(args.arquivo).stem, "—", resposta)]
    else:
        with abrir_db(args.db) as conn:
            envios = carregar_envios(conn, args.submissao)

    if args.todas:
        print("\n".join(resumo_todas(envios)))
        return 0

    envio_id, criado, resposta = envios[0]
    print("\n".join(relatorio(envio_id, criado, resposta)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
