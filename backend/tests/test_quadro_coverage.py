"""
Rastreabilidade entre os quadros da dissertação e o conjunto operacional (§4.4).

A §4.4 registra que houve redução e dá os critérios: "a disponibilidade das
informações, a possibilidade de verificação documental e a comparabilidade entre
os provedores. Alguns indicadores identificados na literatura dependem de dados
internos dos data centers ou não são divulgados de maneira suficientemente
homogênea".

O que estes testes protegem não é o conteúdo da decisão — essa é acadêmica — e sim
que ela esteja **registrada**. Uma linha dos Quadros 22/24 precisa estar coberta
por um indicador ou declarada fora com motivo. Silêncio não é resposta, e é
exatamente o silêncio que produziu a divergência: o Quadro 24 lista
"Monitoramento/auditoria" e o produto não avalia isso, sem que nada no repositório
dissesse por quê.
"""

import json

import pytest

from domain.methodology import MethodologyConfigError, get_methodology, load_methodology

# Linhas do Quadro 22 (Indicadores no processo de inferência), por dimensão.
QUADRO_22 = {
    "sustainability": ["PUE", "DCiE", "ERF", "GEC", "A-PUE", "EFM", "CUE", "SCI"],
    "security": [
        "Nível de segurança",
        "Criptografia (tipos)",
        "Controle de acesso (MFA)",
        "Machine Learning em segurança (NTAF)",
        "Blockchain em segurança",
        "Frameworks de segurança",
    ],
    "performance": [
        "Tempo de resposta (latência)",
        "Disponibilidade",
        "Confiabilidade",
        "Estabilidade",
        "Elasticidade",
        "Rendimento e Eficiência (throughput)",
        "Escalabilidade",
        "Capacidade",
    ],
}

# Linhas do Quadro 24 (Operacionalização dos indicadores). "Informação ausente"
# fica de fora: é regra de tratamento, não indicador.
QUADRO_24 = [
    "Eficiência energética - PUE",
    "DCiE",
    "ERF",
    "Energia renovável",
    "Emissão/intensidade de carbono – CUE/SCI",
    "Práticas ambientais e compromissos",
    "Certificações e frameworks",
    "Criptografia/proteção de dados",
    "IAM/MFA/controle de acesso",
    "Backup e recuperação de desastres",
    "Monitoramento/auditoria",
    "Disponibilidade",
    "Latência",
    "Tempo de resposta",
    "Throughput",
    "Confiabilidade",
    "Escalabilidade",
    "Elasticidade",
    "Suporte técnico",
]


@pytest.fixture
def metodologia():
    return get_methodology()


def _registrado(nome: str, metodologia) -> bool:
    """A linha está coberta por um indicador ou declarada fora do conjunto?"""
    alvo = nome.casefold()
    cobertura = metodologia.quadro_coverage()
    if alvo in {n.casefold() for n in cobertura["quadro_22"] + cobertura["quadro_24"]}:
        return True
    return any(
        alvo in {n.casefold() for n in e.all_names}
        for e in metodologia.not_operationalized
    )


# --- Toda linha da literatura tem destino declarado -------------------------


@pytest.mark.parametrize(
    "nome",
    [n for linhas in QUADRO_22.values() for n in linhas],
)
def test_toda_linha_do_quadro_22_tem_destino(nome, metodologia):
    assert _registrado(nome, metodologia), (
        f"Quadro 22 → {nome!r} não está coberto por indicador nem declarado em "
        "`not_operationalized`."
    )


@pytest.mark.parametrize("nome", QUADRO_24)
def test_toda_linha_do_quadro_24_tem_destino(nome, metodologia):
    assert _registrado(nome, metodologia), (
        f"Quadro 24 → {nome!r} não está coberto por indicador nem declarado em "
        "`not_operationalized`."
    )


# --- O conjunto operacional é o do questionário ----------------------------


def test_conjunto_operacional_espelha_as_perguntas_fechadas(metodologia):
    """
    A §4.4 diz que a plataforma prioriza indicadores identificáveis em fonte
    documental; o Quadro 25 é quem fixa quais chegaram ao produto, porque só
    pergunta respondida produz coeficiente de relevância.
    """
    assert len(metodologia.indicators) == 13
    assert all(i.question_id for i in metodologia.indicators)
    por_dimensao = {d: len(metodologia.by_dimension(d)) for d in metodologia.dimensions}
    assert por_dimensao == {"sustainability": 5, "performance": 4, "security": 4}


def test_todo_indicador_declara_sua_linha_no_quadro_24(metodologia):
    """
    O Quadro 24 descreve a operacionalização de tudo que é avaliado. Indicador
    sem linha lá é indicador que o quadro não descreve — a divergência de origem.
    """
    sem_origem = [i.id for i in metodologia.indicators if not i.quadro_24]
    assert sem_origem == []


def test_todo_indicador_tem_exemplo_de_evidencia(metodologia):
    """Coluna "Exemplo de evidência" do Quadro 24."""
    assert all(i.evidence_example for i in metodologia.indicators)


# --- O que ficou fora ficou fora com motivo --------------------------------


def test_exclusoes_citam_motivo_do_vocabulario(metodologia):
    assert metodologia.not_operationalized
    assert metodologia.exclusion_reasons
    for excluido in metodologia.not_operationalized:
        assert excluido.reason in metodologia.exclusion_reasons
        assert excluido.note, f"{excluido.name}: exclusão sem justificativa concreta."


def test_monitoramento_auditoria_esta_declarado(metodologia):
    """A linha do Quadro 24 que motivou este item do relatório de conformidade."""
    nomes = {e.name for e in metodologia.not_operationalized}
    assert "Monitoramento/auditoria" in nomes


def test_indicadores_sem_linha_no_quadro_22_sao_visiveis(metodologia):
    """
    Quatro indicadores do produto não têm linha no Quadro 22 — vêm do Quadro 25.
    O teste fixa o número para que um quinto não apareça sem decisão.
    """
    orfaos = {i.id for i in metodologia.indicators if not i.quadro_22}
    assert orfaos == {
        "sustainability_ewaste_management",
        "sustainability_circularity",
        "performance_support",
        "security_backup_recovery",
    }


# --- A configuração recusa registro incoerente -----------------------------


def _config(tmp_path, not_operationalized):
    """Configuração mínima de indicadores, com o bloco de exclusões sob teste."""
    caminho = tmp_path / "indicators.json"
    caminho.write_text(
        json.dumps(
            {
                "dimensions": {"security": {"name": "Segurança"}},
                "indicators": [
                    {
                        "id": "security_certifications",
                        "dimension": "security",
                        "name": "Certificações",
                        "question_id": "sec_q1",
                        "data_type": "qualitative",
                        "rubric": "nivel_atendimento",
                        "coverage": {"quadro_24": ["Certificações e frameworks"]},
                    }
                ],
                "not_operationalized": not_operationalized,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return caminho


def test_motivo_fora_do_vocabulario_e_recusado(tmp_path):
    """Motivo livre transformaria a justificativa da §4.4 em texto que ninguém confere."""
    caminho = _config(
        tmp_path,
        {
            "_motivos": {"dados_internos": "…"},
            "items": [{"name": "Throughput", "reason": "achei_dificil"}],
        },
    )
    with pytest.raises(MethodologyConfigError, match="fora do vocabulário"):
        load_methodology(indicators_path=caminho)


def test_linha_coberta_e_excluida_ao_mesmo_tempo_e_recusada(tmp_path):
    """Se as duas afirmações coexistem, uma é falsa — e não se sabe qual."""
    caminho = _config(
        tmp_path,
        {
            "_motivos": {"dados_internos": "…"},
            "items": [
                {"name": "Certificações e frameworks", "reason": "dados_internos"}
            ],
        },
    )
    with pytest.raises(MethodologyConfigError, match="cobertura de um indicador"):
        load_methodology(indicators_path=caminho)


def test_exclusao_sem_nome_e_recusada(tmp_path):
    caminho = _config(tmp_path, {"items": [{"reason": "dados_internos"}]})
    with pytest.raises(MethodologyConfigError, match="sem `name`"):
        load_methodology(indicators_path=caminho)


# --- O gerador e o `--check` ------------------------------------------------


def test_check_do_gerador_passa_na_configuracao_em_vigor(metodologia):
    import importlib.util
    from pathlib import Path

    caminho = Path(__file__).resolve().parents[1] / "scripts" / "generate_quadros.py"
    spec = importlib.util.spec_from_file_location("generate_quadros", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)

    assert modulo.verificar(metodologia) == []

    markdown = "\n".join(modulo.quadro_operacional(metodologia))
    # Todo indicador avaliado aparece no quadro gerado.
    for indicador in metodologia.indicators:
        assert indicador.name in markdown
    # E a linha de informação ausente, que não é indicador, também.
    assert "Informação ausente" in markdown

    cobertura = "\n".join(modulo.quadro_cobertura(metodologia))
    for excluido in metodologia.not_operationalized:
        assert excluido.name in cobertura
