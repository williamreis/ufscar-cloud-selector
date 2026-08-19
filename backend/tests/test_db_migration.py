"""
Migração aditiva do banco de auditoria (diretriz §0, item 5).

"Manter compatibilidade com dados já persistidos quando possível" tem uma
consequência concreta: `create_all` cria tabelas novas mas **não** altera as que
já existem. Sem o passo aditivo, um banco com envios anteriores passa a estourar
`no such column` na primeira consulta depois do deploy.

Estes testes montam um banco no esquema antigo, rodam a migração e conferem que
os envios continuam legíveis.
"""

import importlib
import json
import sqlite3

import pytest

# Esquema como era antes da Fase 0, reduzido ao necessário para a leitura.
ESQUEMA_ANTIGO = """
CREATE TABLE submissions (
    id VARCHAR(32) NOT NULL PRIMARY KEY,
    created_at DATETIME,
    respondent_email VARCHAR(320),
    respondent_role VARCHAR(200),
    session_id VARCHAR(64),
    weight_sustainability FLOAT,
    weight_performance FLOAT,
    weight_security FLOAT,
    lambda_max FLOAT,
    consistency_index FLOAT,
    consistency_ratio FLOAT,
    is_consistent BOOLEAN,
    relevance_sustainability FLOAT,
    relevance_performance FLOAT,
    relevance_security FLOAT,
    top_provider_id VARCHAR(32),
    top_provider_name VARCHAR(120),
    top_provider_score FLOAT,
    llm_notes TEXT,
    llm_provider VARCHAR(40),
    llm_model VARCHAR(120),
    request_json TEXT,
    response_json TEXT
);
CREATE TABLE submission_answers (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    submission_id VARCHAR(32) REFERENCES submissions(id) ON DELETE CASCADE,
    position INTEGER, question_id VARCHAR(64), question_text TEXT,
    choice TEXT, text_answer TEXT
);
CREATE TABLE ahp_judgments (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    submission_id VARCHAR(32) REFERENCES submissions(id) ON DELETE CASCADE,
    question_id VARCHAR(64), criterion_a VARCHAR(40), criterion_b VARCHAR(40),
    ratio FLOAT, choice TEXT
);
CREATE TABLE submission_rankings (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    submission_id VARCHAR(32) REFERENCES submissions(id) ON DELETE CASCADE,
    provider_id VARCHAR(32), provider_name VARCHAR(120),
    rank INTEGER, score FLOAT, contributions_json TEXT
);
"""


@pytest.fixture
def db_antigo(tmp_path, monkeypatch):
    """Banco no esquema antigo, com um envio gravado, e o módulo db apontado para ele."""
    caminho = tmp_path / "audit.db"
    conn = sqlite3.connect(caminho)
    conn.executescript(ESQUEMA_ANTIGO)
    conn.execute(
        "INSERT INTO submissions (id, created_at, respondent_email, respondent_role, "
        "weight_sustainability, weight_performance, weight_security, consistency_ratio, "
        "is_consistent, top_provider_id, top_provider_name, top_provider_score, "
        "request_json, response_json) VALUES "
        "('envio1', '2026-01-15 10:00:00', 'gestor@ufscar.br', 'CTI', "
        "0.5, 0.3, 0.2, 0.05, 1, 'aws', 'AWS', 0.42, ?, ?)",
        (json.dumps({"respondent": "gestor@ufscar.br"}), json.dumps({"ranking": []})),
    )
    conn.execute(
        "INSERT INTO submission_answers (submission_id, position, question_id, choice) "
        "VALUES ('envio1', 0, 'sust_q1', 'Decisivo')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("AUDIT_DB_PATH", str(caminho))
    import db as db_module

    yield importlib.reload(db_module)

    monkeypatch.delenv("AUDIT_DB_PATH", raising=False)
    importlib.reload(db_module)


def _colunas(caminho, tabela):
    conn = sqlite3.connect(caminho)
    try:
        return {linha[1] for linha in conn.execute(f"PRAGMA table_info({tabela})")}
    finally:
        conn.close()


def _tabelas(caminho):
    conn = sqlite3.connect(caminho)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def test_esquema_antigo_nao_tem_as_colunas_novas(db_antigo):
    """Confirma a premissa: sem migração, a leitura quebraria."""
    colunas = _colunas(db_antigo.DB_PATH, "submissions")
    assert "questions_hash" not in colunas
    assert "status" not in colunas


def test_migracao_acrescenta_as_colunas(db_antigo):
    db_antigo.init_db()
    colunas = _colunas(db_antigo.DB_PATH, "submissions")
    for nova in (
        "status",
        "questionnaire_version",
        "questions_hash",
        "algorithm_version",
        "embedding_provider",
        "embedding_model",
    ):
        assert nova in colunas


def test_migracao_cria_as_tabelas_de_auditoria(db_antigo):
    db_antigo.init_db()
    tabelas = _tabelas(db_antigo.DB_PATH)
    for nova in ("llm_runs", "guardrail_events", "rag_queries", "retrieved_chunks", "documents"):
        assert nova in tabelas


def test_envio_anterior_continua_legivel(db_antigo):
    db_antigo.init_db()

    linhas, total = db_antigo.list_submissions()
    assert total == 1
    assert linhas[0]["respondent_email"] == "gestor@ufscar.br"

    envio = db_antigo.get_submission("envio1")
    assert envio["weights"]["sustainability"] == 0.5
    assert envio["top_provider_name"] if "top_provider_name" in envio else True
    assert len(envio["answers"]) == 1


def test_campos_novos_ficam_nulos_no_envio_anterior(db_antigo):
    """Nulo aqui significa 'gravado antes de o campo existir', não 'falhou'."""
    db_antigo.init_db()
    envio = db_antigo.get_submission("envio1")
    assert envio["status"] is None
    assert envio["versions"]["questions_hash"] is None
    assert envio["llm_runs"] == []
    assert envio["guardrail_events"] == []
    assert envio["rag_queries"] == []


def test_migracao_e_idempotente(db_antigo):
    db_antigo.init_db()
    db_antigo.init_db()
    db_antigo.init_db()
    assert db_antigo.list_submissions()[1] == 1


def test_envio_novo_grava_os_blocos_de_auditoria(db_antigo):
    db_antigo.init_db()

    submission_id = db_antigo.save_submission(
        request_payload={"respondent": "novo@ufscar.br", "respondent_role": "TI", "answers": []},
        response_payload={
            "ranking": [{"id": "aws", "name": "AWS", "rank": 1, "score": 0.4}],
            "notes": "texto",
            "versions": {
                "questionnaire_version": "1",
                "questions_hash": "abc123",
                "algorithm_version": "1",
                "llm_provider": "groq",
                "llm_model": "modelo-x",
                "embedding_provider": "huggingface",
                "embedding_model": "all-MiniLM-L6-v2",
            },
        },
        llm_runs=[
            {
                "prompt_id": "PROMPT_PREFERENCE_NOTES_V1",
                "prompt_version": "1",
                "provider": "groq",
                "model": "modelo-x",
                "status": "OK",
                "latency_ms": 120,
                "attempts": 1,
                "input_hash": "h1",
                "output_hash": "h2",
            }
        ],
        guardrail_events=[
            {
                "rule_id": "SECRET_OPENAI_KEY",
                "stage": "input_text",
                "action": "MASK",
                "reason": "credencial detectada",
                "masked_sample": "sk-…01 (<30 caracteres>)",
            }
        ],
        rag_queries=[
            {
                "dimension": "security",
                "provider_id": "aws",
                "query_text": "AWS: ISO 27001",
                "top_k": 2,
                "chunks": [
                    {"chunk_id": "c1", "document_id": "d1", "file_name": "aws.pdf", "page": 4, "score": 0.31}
                ],
            }
        ],
        status="COMPLETED",
    )

    envio = db_antigo.get_submission(submission_id)
    assert envio["status"] == "COMPLETED"
    assert envio["versions"]["questions_hash"] == "abc123"
    assert envio["versions"]["embedding_model"] == "all-MiniLM-L6-v2"
    assert envio["llm_runs"][0]["prompt_id"] == "PROMPT_PREFERENCE_NOTES_V1"
    assert envio["guardrail_events"][0]["rule_id"] == "SECRET_OPENAI_KEY"
    assert envio["rag_queries"][0]["chunks"][0]["chunk_id"] == "c1"


def test_documento_e_idempotente_por_conteudo(db_antigo):
    """document_id é o hash do conteúdo: reingerir o mesmo arquivo não duplica."""
    db_antigo.init_db()
    detalhe = {
        "document_id": "abc",
        "file_name": "aws-2025.pdf",
        "provider": "aws",
        "year": 2025,
        "scope": "global",
        "chunks": 12,
    }
    db_antigo.save_documents([detalhe])
    db_antigo.save_documents([{**detalhe, "chunks": 15}])

    documentos = db_antigo.list_documents()
    assert len(documentos) == 1
    assert documentos[0]["chunk_count"] == 15
