"""
Identidade e metadados dos trechos indexados (diretriz §14.3).

Antes da Fase 0 um chunk não tinha nome. Sem `chunk_id` a validação de
proveniência da §19 é impossível: não há como exigir que a evidência devolvida
pela LLM aponte para um trecho que de fato lhe foi entregue.

Os identificadores são derivados do conteúdo, e é isso que estes testes fixam —
reconstruir o índice não pode renomear os trechos, senão toda evidência gravada
passa a apontar para o vazio.
"""

from rag.metadata import (
    SCOPE_GLOBAL,
    build_chunk_metadata,
    build_document_metadata,
    chunk_id_for,
    document_id_for,
    evaluation_scope,
    year_from_name,
)

CONTEUDO = b"%PDF-1.7 relatorio de sustentabilidade"


def _documento(nome="2025-aws-sustainability-report.pdf", scope=SCOPE_GLOBAL, **kwargs):
    return build_document_metadata(file_name=nome, content=CONTEUDO, scope=scope, **kwargs)


# --- Identidade determinística ---------------------------------------------


def test_mesmo_conteudo_produz_o_mesmo_document_id():
    assert document_id_for(CONTEUDO) == document_id_for(bytes(CONTEUDO))


def test_conteudo_diferente_produz_document_id_diferente():
    assert document_id_for(CONTEUDO) != document_id_for(CONTEUDO + b" v2")


def test_chunk_id_sobrevive_a_reconstrucao_do_indice():
    """Reingerir o mesmo arquivo tem de devolver os mesmos ids."""
    primeira = build_chunk_metadata(_documento(), index=3, text="PUE de 1,12")
    segunda = build_chunk_metadata(_documento(), index=3, text="PUE de 1,12")
    assert primeira["chunk_id"] == segunda["chunk_id"]
    assert primeira["content_hash"] == segunda["content_hash"]


def test_trechos_identicos_no_mesmo_documento_sao_distinguiveis():
    """Cabeçalho repetido não pode colapsar em um único id."""
    documento = _documento()
    a = build_chunk_metadata(documento, index=0, text="Relatório 2025")
    b = build_chunk_metadata(documento, index=7, text="Relatório 2025")
    assert a["chunk_id"] != b["chunk_id"]


def test_chunk_id_muda_com_o_texto():
    documento = _documento()
    a = build_chunk_metadata(documento, index=0, text="PUE de 1,12")
    b = build_chunk_metadata(documento, index=0, text="PUE de 1,20")
    assert a["chunk_id"] != b["chunk_id"]


def test_chunk_id_e_derivado_do_documento():
    assert chunk_id_for("doc-a", 0, "x") != chunk_id_for("doc-b", 0, "x")


# --- Campos exigidos pela §14.3 --------------------------------------------


def test_documento_traz_os_campos_da_diretriz():
    metadata = _documento()
    for campo in (
        "document_id",
        "source_name",
        "source_type",
        "source_url",
        "provider_id",
        "year",
        "scope",
        "ingested_at",
    ):
        assert campo in metadata


def test_chunk_traz_identidade_e_proveniencia():
    metadata = build_chunk_metadata(_documento(), index=0, text="x", page=11, page_label="12")
    for campo in ("chunk_id", "document_id", "content_hash", "page", "page_number", "scope"):
        assert campo in metadata


def test_campo_sem_informacao_fica_nulo_e_nao_e_inventado():
    """§14.3: quando a informação não existir, usar null."""
    metadata = _documento(nome="relatorio-sem-ano.pdf")
    assert metadata["year"] is None
    assert metadata["source_type"] is None
    assert metadata["source_url"] is None


# --- Ano e página ----------------------------------------------------------


def test_ano_e_lido_do_nome_do_arquivo():
    assert year_from_name("2025-azure-environmental-report.pdf") == 2025


def test_numero_de_norma_nao_vira_ano():
    """ISO 27001 não é data — a faixa existe para isso."""
    assert year_from_name("iso-27001-certificado.pdf") is None
    assert year_from_name("relatorio-v1.2.pdf") is None


def test_pagina_preserva_a_base_do_loader_e_expoe_a_humana():
    """
    O índice já persistido guarda a página em base 0; reinterpretá-la deslocaria
    em uma toda citação anterior. Por isso as duas formas convivem.
    """
    metadata = build_chunk_metadata(_documento(), index=0, text="x", page=11)
    assert metadata["page"] == 11
    assert metadata["page_number"] == 12


def test_pagina_ausente_nao_vira_zero():
    metadata = build_chunk_metadata(_documento(), index=0, text="x", page=None)
    assert metadata["page"] is None and metadata["page_number"] is None


# --- Isolamento de escopo (§14.2) ------------------------------------------


def test_escopo_global_e_de_avaliacao_sao_distintos():
    assert evaluation_scope("sessao-1") == "evaluation:sessao-1"
    assert evaluation_scope("sessao-1") != evaluation_scope("sessao-2")


def test_documento_de_avaliacao_nao_se_apresenta_como_global():
    da_sessao = _documento(scope=evaluation_scope("s1"), session_id="s1")
    assert da_sessao["scope"] == "evaluation:s1"
    # Chave de compatibilidade usada pelos filtros do índice já persistido.
    assert da_sessao["source"] == "session"
    assert da_sessao["session_id"] == "s1"


def test_documento_global_mantem_o_filtro_antigo():
    assert _documento()["source"] == SCOPE_GLOBAL
