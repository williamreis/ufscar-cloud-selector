"""
Ingestão documental pela área de gestão (`/api/admin/rag/*`).

O que estes testes cobrem é a costura, não o pipeline: que a rota exige token,
que o inventário distingue arquivo pendente de arquivo já indexado, que a
seleção por nome não escapa de data/pdf e que a ingestão registra o documento no
banco de auditoria.

`rag.ingest_paths` é substituído por um duplo — a suíte não baixa modelo de
embedding nem constrói índice FAISS.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

SENHA = "senha-de-teste"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", SENHA)
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("PDF_DIR", str(tmp_path / "pdf"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "upload"))

    import config

    config.reload_settings()

    import db

    importlib.reload(db)
    import documents

    importlib.reload(documents)
    import main

    importlib.reload(main)

    with TestClient(main.app) as test_client:
        test_client.documents = documents
        test_client.db = db
        test_client.pdf_dir = documents.pdf_dir()
        yield test_client


def _token(client) -> dict:
    resposta = client.post("/api/admin/login", json={"password": SENHA})
    assert resposta.status_code == 200
    return {"Authorization": f"Bearer {resposta.json()['token']}"}


def _documento(client, nome: str = "aws-sustainability-2025.txt", texto: str = "PUE 1,15") -> None:
    (client.pdf_dir / nome).write_text(texto, encoding="utf-8")


def _duplo_de_ingestao(client, monkeypatch, chunks: int = 7):
    """Substitui o pipeline por um retorno no mesmo formato de `rag.ingest_paths`."""
    chamadas = []

    def falso_ingest_paths(paths, scope, session_id=None, source_type=None, guardrail_log=None):
        chamadas.append(list(paths))
        detalhes = [
            {
                "file": caminho,
                "file_name": caminho.rsplit("/", 1)[-1],
                "document_id": client.documents.document_id_for(open(caminho, "rb").read()),
                "chunks": chunks,
                "provider": "aws",
                "year": 2025,
                "scope": scope,
            }
            for caminho in paths
        ]
        return {
            "chunks": chunks * len(paths),
            "files_processed": len(paths),
            "files_failed": 0,
            "details": detalhes,
            "unassigned_files": [],
            "documents": [],
            "errors": [],
        }

    monkeypatch.setattr(client.documents.rag, "ingest_paths", falso_ingest_paths)
    return chamadas


# --- Autenticação ----------------------------------------------------------


def test_status_e_ingestao_exigem_token(client):
    assert client.get("/api/admin/rag/status").status_code == 401
    assert client.post("/api/admin/rag/ingest").status_code == 401


# --- Inventário ------------------------------------------------------------


def test_status_lista_arquivo_ainda_nao_indexado(client):
    _documento(client)
    corpo = client.get("/api/admin/rag/status", headers=_token(client)).json()

    assert corpo["pending_files"] == 1
    arquivo = corpo["files"][0]
    assert arquivo["name"] == "aws-sustainability-2025.txt"
    assert arquivo["indexed"] is False
    assert arquivo["provider_id"] == "aws"
    assert arquivo["year"] == 2025
    # Sem ingestão não há índice: o painel diz isso em vez de deduzir do banco.
    assert corpo["index_ready"] is False
    assert corpo["chunks_total"] == 0


def test_status_traz_todos_os_provedores_inclusive_os_sem_documento(client):
    corpo = client.get("/api/admin/rag/status", headers=_token(client)).json()
    assert [p["chunks"] for p in corpo["providers"]] == [0] * len(corpo["providers"])
    assert {p["id"] for p in corpo["providers"]} >= {"aws", "gcp", "azure"}


def test_arquivo_sem_provedor_no_nome_e_sinalizado(client):
    _documento(client, nome="relatorio-generico.txt")
    corpo = client.get("/api/admin/rag/status", headers=_token(client)).json()
    assert corpo["unassigned_files"] == ["relatorio-generico.txt"]
    assert corpo["files"][0]["provider_id"] is None


# --- Execução da ingestão --------------------------------------------------


def test_ingestao_indexa_o_diretorio_e_registra_o_documento(client, monkeypatch):
    _documento(client)
    chamadas = _duplo_de_ingestao(client, monkeypatch)
    headers = _token(client)

    corpo = client.post("/api/admin/rag/ingest", headers=headers).json()
    assert corpo["files_processed"] == 1
    assert corpo["chunks"] == 7
    assert len(chamadas[0]) == 1

    depois = client.get("/api/admin/rag/status", headers=headers).json()
    assert depois["pending_files"] == 0
    assert depois["files"][0]["indexed"] is True
    assert depois["files"][0]["chunks"] == 7
    assert depois["documents_indexed"] == 1


def test_ingestao_aceita_selecao_de_arquivos(client, monkeypatch):
    _documento(client, nome="aws-2025.txt")
    _documento(client, nome="azure-2025.txt")
    chamadas = _duplo_de_ingestao(client, monkeypatch)

    resposta = client.post(
        "/api/admin/rag/ingest", headers=_token(client), json={"files": ["azure-2025.txt"]}
    )
    assert resposta.status_code == 200
    assert [caminho.rsplit("/", 1)[-1] for caminho in chamadas[0]] == ["azure-2025.txt"]


def test_selecao_com_caminho_para_fora_de_data_pdf_e_recusada(client, monkeypatch):
    _documento(client)
    _duplo_de_ingestao(client, monkeypatch)
    resposta = client.post(
        "/api/admin/rag/ingest", headers=_token(client), json={"files": ["../upload/x.txt"]}
    )
    # O nome é reduzido ao arquivo final e não existe em data/pdf.
    assert resposta.status_code in (400, 404)


def test_selecao_de_arquivo_inexistente_devolve_404(client, monkeypatch):
    _duplo_de_ingestao(client, monkeypatch)
    resposta = client.post(
        "/api/admin/rag/ingest", headers=_token(client), json={"files": ["nao-existe.pdf"]}
    )
    assert resposta.status_code == 404


def test_diretorio_vazio_responde_sem_erro(client):
    corpo = client.post("/api/admin/rag/ingest", headers=_token(client)).json()
    assert corpo["chunks"] == 0
    assert corpo["files_processed"] == 0
    assert "data/pdf" in corpo["message"]


def test_total_de_trechos_inclui_documento_sem_provedor(client, monkeypatch):
    """
    A soma por provedor ignora o trecho sem provedor atribuído; o total do painel
    não pode ignorar — ele responde "o que existe no índice", não "o que vira
    evidência de alguém".
    """

    class TrechoFalso:
        def __init__(self, provedor):
            self.metadata = {"provider_id": provedor} if provedor else {}

    class DocstoreFalso:
        _dict = {"a": TrechoFalso("aws"), "b": TrechoFalso("aws"), "c": TrechoFalso(None)}

    class IndiceFalso:
        docstore = DocstoreFalso()

    monkeypatch.setattr(client.documents.rag.index, "load", lambda use_cache=True: IndiceFalso())

    corpo = client.get("/api/admin/rag/status", headers=_token(client)).json()
    assert corpo["chunks_total"] == 3
    assert next(p for p in corpo["providers"] if p["id"] == "aws")["chunks"] == 2
