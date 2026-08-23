#!/usr/bin/env python3
"""
Gera os quadros de indicadores a partir da configuração em vigor.

Existe por causa de uma divergência real: os Quadros 22 e 24 da dissertação
listam indicadores que o produto não avalia (Monitoramento/auditoria, throughput,
confiabilidade, blockchain…), e não listam alguns que ele avalia (resíduos
eletrônicos, circularidade, suporte técnico). Transcrever a correção à mão traria
o mesmo problema de volta na próxima mudança.

Aqui os quadros saem de `indicators.json` e `scales.json` — as mesmas fontes que
o cálculo lê. Se um indicador entrar ou sair do produto, o quadro muda junto.

    python scripts/generate_quadros.py            # Markdown no stdout
    python scripts/generate_quadros.py --check    # falha se algo não está registrado

O `--check` serve à integração contínua: ele não formata nada, apenas confirma
que toda linha dos Quadros 22 e 24 está ou coberta por um indicador ou declarada
em `not_operationalized`. Uma linha em nenhum dos dois é lacuna de registro.
"""

import argparse
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from domain.methodology import Methodology, get_methodology  # noqa: E402

DIMENSAO_ORDEM = ("sustainability", "performance", "security")

SENTIDO = {
    "benefit": "Maior é melhor",
    "minimize": "Menor é melhor",
    None: "Maior atendimento é melhor",
}

REGRA = {
    "benefit": "Normalização por benefício",
    "minimize": "Normalização por minimização",
}


def _escape(texto: str) -> str:
    """Barra vertical dentro de célula quebraria a tabela Markdown."""
    return texto.replace("|", "\\|")


def quadro_operacional(m: Methodology) -> List[str]:
    """Quadro 24 — operacionalização dos indicadores, com o conjunto real."""
    linhas = [
        "### Quadro 24 — Operacionalização dos indicadores no modelo de avaliação",
        "",
        "| Dimensão | Indicador | Natureza | Sentido | Regra de tratamento | Exemplo de evidência |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    for dimensao in DIMENSAO_ORDEM:
        for indicador in m.by_dimension(dimensao):
            natureza = "Quantitativo" if indicador.is_quantitative else "Qualitativo"
            regra = REGRA.get(
                indicador.direction,
                f"Regra determinística de avaliação qualitativa (Quadro 23, rubrica "
                f"`{indicador.rubric.name}`)" if indicador.rubric else "—",
            )
            linhas.append(
                "| {} | {} | {} | {} | {} | {} |".format(
                    _escape(m.dimension_name(dimensao)),
                    _escape(indicador.name),
                    natureza,
                    SENTIDO.get(indicador.direction, SENTIDO[None]),
                    _escape(regra),
                    _escape(indicador.evidence_example or "—"),
                )
            )

    # A linha "Informação ausente" do Quadro 24 vale para todas as dimensões e
    # não é um indicador — por isso não sai de `indicators`, mas precisa constar.
    linhas.append(
        "| Todas | Informação ausente | Não aplicável | Não aplicável | "
        "Não atribuir zero; excluir o indicador da avaliação de todas as alternativas e "
        "renormalizar proporcionalmente os pesos dos indicadores válidos | "
        "“não identificado nas fontes recuperadas” |"
    )
    return linhas


def quadro_cobertura(m: Methodology) -> List[str]:
    """Rastreabilidade: o que da literatura entrou, e o que ficou fora e por quê."""
    linhas = [
        "### Cobertura dos indicadores identificados na literatura",
        "",
        "| Indicador (Quadros 22/24) | Situação | Onde entrou / por que ficou fora |",
        "| --- | --- | --- |",
    ]

    for dimensao in DIMENSAO_ORDEM:
        for indicador in m.by_dimension(dimensao):
            origens = list(dict.fromkeys((*indicador.quadro_22, *indicador.quadro_24)))
            if not origens:
                continue
            linhas.append(
                "| {} | Operacionalizado | {} |".format(
                    _escape(", ".join(origens)),
                    _escape(indicador.name),
                )
            )

    for excluido in m.not_operationalized:
        motivo = m.exclusion_reasons.get(excluido.reason, excluido.reason)
        detalhe = f"{motivo}. {excluido.note}" if excluido.note else motivo
        linhas.append(
            "| {} | Não operacionalizado | {} |".format(
                _escape(excluido.name), _escape(detalhe)
            )
        )

    return linhas


def indicadores_sem_origem(m: Methodology) -> List[str]:
    """Indicadores do produto que não têm linha própria nos quadros da literatura."""
    linhas = [
        "### Indicadores do produto sem linha própria no Quadro 22",
        "",
        "Vêm do questionário (Quadro 25) e das Revisões Sistemáticas, e são avaliados",
        "pela plataforma. O Quadro 22 precisa acomodá-los ou o texto precisa dizer que",
        "o conjunto operacional é o do Quadro 25.",
        "",
    ]
    orfaos = [i for i in m.indicators if not i.quadro_22]
    if not orfaos:
        linhas.append("_Nenhum._")
        return linhas
    for indicador in orfaos:
        linhas.append(
            f"- **{indicador.name}** ({m.dimension_name(indicador.dimension)}) — "
            f"pergunta `{indicador.question_id}`"
        )
    return linhas


def verificar(m: Methodology) -> List[str]:
    """Problemas de registro. Lista vazia = tudo rastreado."""
    problemas: List[str] = []

    for indicador in m.indicators:
        if not indicador.evidence_example:
            problemas.append(f"{indicador.id}: sem `coverage.evidence_example`.")
        if not indicador.quadro_24:
            problemas.append(
                f"{indicador.id}: sem `coverage.quadro_24` — o Quadro 24 descreve a "
                "operacionalização de todo indicador avaliado."
            )

    sem_motivo = [e.name for e in m.not_operationalized if not e.reason]
    if sem_motivo:
        problemas.append(
            "Linhas fora do conjunto sem motivo declarado: " + ", ".join(sem_motivo)
        )

    return problemas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Não gera texto; falha se algum indicador não está rastreado.",
    )
    args = parser.parse_args()

    m = get_methodology()
    problemas = verificar(m)

    if args.check:
        if problemas:
            print("Registro incompleto:", file=sys.stderr)
            for p in problemas:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print(
            f"OK — {len(m.indicators)} indicadores rastreados, "
            f"{len(m.not_operationalized)} linhas declaradas fora do conjunto."
        )
        return 0

    versao = m.fingerprint()
    blocos = [
        "<!-- Gerado por scripts/generate_quadros.py — não editar à mão. -->",
        f"<!-- indicators v{versao['indicators_version']} · "
        f"hash {versao['indicators_hash'][:12]} -->",
        "",
        *quadro_operacional(m),
        "",
        *quadro_cobertura(m),
        "",
        *indicadores_sem_origem(m),
    ]
    if problemas:
        blocos += ["", "### Pendências de registro", ""]
        blocos += [f"- {p}" for p in problemas]

    print("\n".join(blocos))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
