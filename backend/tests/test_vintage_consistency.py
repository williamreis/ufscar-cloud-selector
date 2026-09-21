"""
Safra da evidência (comparabilidade temporal).

A §4.4.1.1 exige valores "mensuráveis e comparáveis", e o produto já retira da
comparação o indicador cujos provedores publicam em **unidades** diferentes.
Período diferente é o mesmo problema por outra porta e estava desprotegido:
medido no acervo em uso, a taxa de desvio de aterro comparava 2024 da AWS com
FY22 do Azure, e o percentual de energia renovável ia de 2022 a 2025.

Estes testes fixam as três decisões: a tolerância absorve a defasagem normal de
publicação, acima dela o indicador sai inteiro (não só o provedor atrasado), e
`None` desliga a regra sem apagar o registro da safra.
"""

import pytest

from evidence import Finding, enforce_vintage_consistency
from guardrails import GuardrailLog
from domain.normalization import STATUS_FOUND, STATUS_INVALID, STATUS_NOT_FOUND


def achado(provider_id, ano, indicator_id="sustainability_ewaste_management", status=STATUS_FOUND):
    return Finding(
        provider_id=provider_id,
        indicator_id=indicator_id,
        dimension="sustainability",
        status=status,
        value=80.0,
        unit="%",
        source_chunk_id=f"c-{provider_id}",
        source_year=ano,
    )


@pytest.fixture
def log():
    return GuardrailLog()


def test_um_ano_de_diferenca_e_defasagem_de_publicacao(log):
    """Relatórios anuais não saem no mesmo mês; um ano não invalida a comparação."""
    findings = [achado("aws", 2024), achado("gcp", 2024), achado("azure", 2023)]
    saida = enforce_vintage_consistency(findings, log, tolerance_years=1)
    assert {f.status for f in saida} == {STATUS_FOUND}


def test_dois_anos_retiram_o_indicador(log):
    """Foi o caso real: AWS em 2024 contra Azure em FY22."""
    findings = [achado("aws", 2024), achado("gcp", 2023), achado("azure", 2022)]
    saida = enforce_vintage_consistency(findings, log, tolerance_years=1)
    assert {f.status for f in saida} == {STATUS_INVALID}
    assert all("2022 a 2024" in (f.rejection or "") for f in saida)


def test_a_exclusao_atinge_todos_os_provedores(log):
    """
    §11.1: retirar só o provedor desatualizado deixaria os demais numa régua da
    qual ele saiu por um motivo que não é desempenho.
    """
    findings = [achado("aws", 2026), achado("gcp", 2026), achado("azure", 2022)]
    saida = enforce_vintage_consistency(findings, log, tolerance_years=1)
    assert {f.provider_id for f in saida if f.status == STATUS_INVALID} == {"aws", "gcp", "azure"}


def test_tolerancia_nula_desliga_a_regra(log):
    findings = [achado("aws", 2026), achado("azure", 2019)]
    saida = enforce_vintage_consistency(findings, log, tolerance_years=None)
    assert {f.status for f in saida} == {STATUS_FOUND}
    assert [f.source_year for f in saida] == [2026, 2019]


def test_indicador_sem_ano_conhecido_nao_e_afetado(log):
    """Documento sem ano no nome não deve derrubar a comparação por omissão."""
    findings = [achado("aws", None), achado("azure", None)]
    saida = enforce_vintage_consistency(findings, log, tolerance_years=1)
    assert {f.status for f in saida} == {STATUS_FOUND}


def test_evidencia_ausente_nao_entra_na_conta(log):
    """
    Só a evidência encontrada define a janela. Um `NOT_FOUND` não tem safra e não
    pode arrastar o indicador para fora por um ano que ele não tem.
    """
    findings = [achado("aws", 2026), achado("gcp", None, status=STATUS_NOT_FOUND), achado("azure", 2026)]
    saida = enforce_vintage_consistency(findings, log, tolerance_years=1)
    assert [f.status for f in saida] == [STATUS_FOUND, STATUS_NOT_FOUND, STATUS_FOUND]


def test_a_exclusao_e_registrada_no_log(log):
    enforce_vintage_consistency(
        [achado("aws", 2026), achado("azure", 2022)], log, tolerance_years=1
    )
    assert any(e.rule_id == "EVIDENCE_VINTAGE_MISMATCH" for e in log.events)


# --- O motivo relatado precisa ser o motivo real ----------------------------


def test_indicador_invalidado_nao_e_relatado_como_sem_evidencia():
    """
    Um indicador retirado por safra (ou por unidade) tem evidência — ela é que
    não é comparável. Relatá-lo como "sem evidência" mandaria o gestor procurar
    documento que já existe, em vez de procurar documento do mesmo período.
    """
    from domain.methodology import get_methodology
    from domain.normalization import (
        EXCLUDED_INVALID_FOR_COMPARISON,
        PerformanceInput,
        build_comparability_set,
    )

    metodologia = get_methodology()
    indicador = "sustainability_ewaste_management"
    provedores = ["aws", "gcp", "azure"]

    invalidados = [
        PerformanceInput(
            provider_id=pid, indicator_id=indicador, status=STATUS_INVALID, value=None
        )
        for pid in provedores
    ]
    conjunto = build_comparability_set(
        invalidados, provedores, [indicador], metodologia
    )
    assert conjunto.excluded[indicador] == EXCLUDED_INVALID_FOR_COMPARISON


def test_invalido_isolado_entre_ausencias_continua_sendo_falta_de_evidencia():
    """
    Um provedor cuja evidência foi recusada, com os outros dois sem documento
    algum, é falta de evidência — não incomparabilidade. Rotular ao contrário
    esconderia que a maior parte do acervo simplesmente não cobre o indicador.
    """
    from domain.methodology import get_methodology
    from domain.normalization import (
        EXCLUDED_NO_EVIDENCE,
        PerformanceInput,
        build_comparability_set,
    )

    indicador = "performance_latency"
    provedores = ["aws", "gcp", "azure"]
    entradas = [
        PerformanceInput(provider_id="aws", indicator_id=indicador, status=STATUS_INVALID),
        PerformanceInput(provider_id="gcp", indicator_id=indicador, status=STATUS_NOT_FOUND),
        PerformanceInput(provider_id="azure", indicator_id=indicador, status=STATUS_NOT_FOUND),
    ]
    conjunto = build_comparability_set(entradas, provedores, [indicador], get_methodology())
    assert conjunto.excluded[indicador] == EXCLUDED_NO_EVIDENCE
