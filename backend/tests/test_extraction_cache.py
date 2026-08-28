"""
Cache de extração: mesma pergunta sobre os mesmos trechos, mesma leitura.

O que estes testes protegem é a estabilidade **e os seus limites**. Guardar a
leitura resolve o não-determinismo do modelo, mas erraria feio se respondesse
para trechos diferentes, prompt diferente ou modelo diferente — aí não é a mesma
pergunta, e devolver a leitura antiga seria atribuir a um documento algo que foi
lido de outro.
"""

import json

import pytest

import db


@pytest.fixture(autouse=True)
def banco_limpo(tmp_path, monkeypatch):
    """Banco próprio por teste, para o cache não vazar entre eles."""
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    import importlib

    importlib.reload(db)
    db.init_db()
    yield
    importlib.reload(db)


def _guardar(chave, resposta='{"findings": []}', **kw):
    base = dict(
        cache_key=chave,
        provider_id="aws",
        dimension="security",
        chunks_hash="hash-1",
        prompt_id="PROMPT_EVIDENCE_EXTRACTION_V1",
        prompt_version="6",
        model="gpt-4o-mini",
        raw_response=resposta,
    )
    base.update(kw)
    db.save_cached_extraction(**base)


def test_leitura_guardada_volta_igual():
    _guardar("k1", '{"findings": [{"indicator_id": "security_iam"}]}')
    assert json.loads(db.get_cached_extraction("k1"))["findings"][0]["indicator_id"] == "security_iam"


def test_chave_inexistente_devolve_nulo():
    assert db.get_cached_extraction("nao-existe") is None


def test_chave_muda_com_os_trechos_entregues():
    """
    Recuperação diferente é evidência diferente. Se a chave ignorasse os trechos,
    uma leitura feita sobre o whitepaper de segurança responderia a uma pergunta
    feita sobre o relatório ambiental.
    """
    a = db.extraction_cache_key("aws", "security", "hash-A", "P", "6", "m")
    b = db.extraction_cache_key("aws", "security", "hash-B", "P", "6", "m")
    assert a != b


def test_chave_muda_com_a_versao_do_prompt():
    """
    Foi o prompt v6 que passou a pedir o rótulo do bloco em vez do hash. Uma
    leitura feita sob o v5 responde a outras regras e não vale para o v6.
    """
    v5 = db.extraction_cache_key("aws", "security", "h", "P", "5", "m")
    v6 = db.extraction_cache_key("aws", "security", "h", "P", "6", "m")
    assert v5 != v6


def test_chave_muda_com_o_modelo():
    a = db.extraction_cache_key("aws", "security", "h", "P", "6", "gpt-4o-mini")
    b = db.extraction_cache_key("aws", "security", "h", "P", "6", "nemotron")
    assert a != b


def test_chave_muda_com_provedor_e_dimensao():
    base = ("h", "P", "6", "m")
    assert db.extraction_cache_key("aws", "security", *base) != db.extraction_cache_key(
        "gcp", "security", *base
    )
    assert db.extraction_cache_key("aws", "security", *base) != db.extraction_cache_key(
        "aws", "performance", *base
    )


def test_campos_nao_se_confundem_entre_si():
    """
    Sem separador, ('ab','c') e ('a','bc') virariam o mesmo material de hash — e
    duas perguntas distintas dividiriam a mesma leitura.
    """
    a = db.extraction_cache_key("ab", "c", "h", "P", "6", "m")
    b = db.extraction_cache_key("a", "bc", "h", "P", "6", "m")
    assert a != b


def test_impressao_dos_trechos_depende_da_ordem():
    """
    O prompt numera os blocos (T1, T2…) e a evidência cita esse número. Os mesmos
    trechos em ordem diferente são outro contexto: reaproveitar a leitura faria a
    citação apontar para o trecho errado.
    """
    assert db.chunks_fingerprint(["a", "b"]) != db.chunks_fingerprint(["b", "a"])
    assert db.chunks_fingerprint(["a", "b"]) == db.chunks_fingerprint(["a", "b"])


def test_gravar_duas_vezes_mantem_a_primeira_leitura():
    """
    Vale a primeira. Sobrescrever devolveria pela porta dos fundos a oscilação
    que o cache existe para eliminar.
    """
    _guardar("k1", '{"findings": ["primeira"]}')
    _guardar("k1", '{"findings": ["segunda"]}')
    assert "primeira" in db.get_cached_extraction("k1")


def test_acertos_sao_contados():
    _guardar("k1")
    db.get_cached_extraction("k1")
    db.get_cached_extraction("k1")
    assert db.extraction_cache_stats()["hits"] == 2


def test_estatisticas_agrupam_por_versao_de_prompt():
    _guardar("k5", prompt_version="5")
    _guardar("k6", prompt_version="6")
    stats = db.extraction_cache_stats()
    assert stats["entries"] == 2
    assert stats["by_prompt_version"] == {"5": 1, "6": 1}


def test_limpar_o_cache_esvazia():
    _guardar("k1")
    _guardar("k2", chunks_hash="hash-2")
    assert db.clear_extraction_cache() == 2
    assert db.extraction_cache_stats()["entries"] == 0
    assert db.get_cached_extraction("k1") is None


# --- Integração com a extração -------------------------------------------


class _LLMInstavel:
    """
    LLM que responde diferente a cada chamada — o comportamento que o cache
    existe para neutralizar. Se a extração consultar o modelo duas vezes para a
    mesma pergunta, as duas leituras divergem e o teste percebe.
    """

    def __init__(self):
        self.chamadas = 0

    async def structured_generate(self, prompt, schema, max_attempts=2):
        from llm.client import LLMRunRecord, StructuredResult

        self.chamadas += 1
        categoria = "alto" if self.chamadas == 1 else "moderado"
        dados = schema.model_validate(
            {
                "findings": [
                    {
                        "indicator_id": "security_iam",
                        "evidence_status": "FOUND",
                        "nature": "qualitative",
                        "category": categoria,
                        "extracted_value": "IAM com MFA",
                        "summary": "Controle de acesso com autenticação multifator.",
                        "source_chunk_id": "T1",
                        "source_document": "seguranca.pdf",
                    }
                ]
            }
        )
        # `ok` é propriedade derivada de `data` + `run.status`; não se passa.
        return StructuredResult(
            data=dados,
            run=LLMRunRecord(
                run_id="r", prompt_id="P", prompt_version="6", provider="fake",
                model="fake-model", status="OK", latency_ms=1, attempts=1, input_hash="h",
            ),
        )


def _chunks(chunk_id="chunk-1"):
    return [
        {
            "page_content": "IAM com autenticação multifator e privilégio mínimo.",
            "chunk_id": chunk_id,
            "file_name": "seguranca.pdf",
            "page": 2,
        }
    ]


async def _extrair(cliente, chunks):
    import asyncio

    import evidence
    from domain.methodology import load_methodology

    metodologia = load_methodology()
    indicador = metodologia.by_id("security_iam")
    from guardrails import GuardrailLog

    return await evidence._extract_dimension(
        {"id": "aws", "name": "AWS"},
        "security",
        [indicador],
        chunks,
        metodologia,
        GuardrailLog(),
        asyncio.Semaphore(1),
    )


def test_segunda_extracao_reaproveita_a_leitura(monkeypatch):
    """
    O caso medido: 4 das 39 células oscilavam entre execuções e derrubavam o
    indicador inteiro pela §11.1. Com cache, a segunda avaliação lê o mesmo.
    """
    import asyncio

    import evidence

    cliente = _LLMInstavel()
    monkeypatch.setattr(evidence, "get_llm_client", lambda: cliente)

    primeiro, run1 = asyncio.run(_extrair(cliente, _chunks()))
    segundo, run2 = asyncio.run(_extrair(cliente, _chunks()))

    assert cliente.chamadas == 1, "a segunda extração não podia chamar a LLM"
    assert primeiro[0].category == segundo[0].category == "alto"
    assert run2 is None, "leitura em cache não produz execução de LLM para auditar"


def test_trechos_diferentes_nao_reaproveitam(monkeypatch):
    """Outra evidência, outra pergunta: o cache não pode responder."""
    import asyncio

    import evidence

    cliente = _LLMInstavel()
    monkeypatch.setattr(evidence, "get_llm_client", lambda: cliente)

    asyncio.run(_extrair(cliente, _chunks("chunk-1")))
    asyncio.run(_extrair(cliente, _chunks("chunk-2")))

    assert cliente.chamadas == 2


def test_validacao_roda_tambem_no_caminho_do_cache(monkeypatch):
    """
    O cache guarda a resposta BRUTA. A §19, a rubrica e a normalização precisam
    tornar a rodar — senão uma leitura guardada entraria no cálculo por uma porta
    que a validação não vigia.
    """
    import asyncio

    import evidence
    from domain.normalization import STATUS_FOUND

    cliente = _LLMInstavel()
    monkeypatch.setattr(evidence, "get_llm_client", lambda: cliente)

    asyncio.run(_extrair(cliente, _chunks()))
    segundo, _ = asyncio.run(_extrair(cliente, _chunks()))

    achado = segundo[0]
    assert achado.status == STATUS_FOUND
    # 'alto' → 0,75 pela rubrica do Quadro 23, aplicada na leitura em cache.
    assert achado.value == pytest.approx(0.75)
    # O rótulo T1 foi resolvido para o chunk_id real, como no caminho normal.
    assert achado.source_chunk_id == "chunk-1"
