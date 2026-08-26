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
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SENHA = "senha-de-teste"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", SENHA)
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("PDF_DIR", str(tmp_path / "pdf"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "upload"))
    # A limpeza da base apaga arquivos de verdade: sem isolar o caminho, um teste
    # de `/rag/reset` apagaria o índice da instalação em que a suíte roda.
    monkeypatch.setenv("VECTOR_DB_PATH", str(tmp_path / "faiss_index"))

    import config

    config.reload_settings()

    import db

    importlib.reload(db)
    import documents

    importlib.reload(documents)
    import main

    importlib.reload(main)

    # O job vive no módulo: sem reiniciar, um teste herdaria o estado do anterior.
    documents._job.update(
        {
            "state": documents.STATE_IDLE,
            "job_id": None,
            "files": [],
            "started_at": None,
            "finished_at": None,
            "progress": {"done": 0, "total": 0, "current": None},
            "result": None,
            "error": None,
        }
    )

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


def _aguarda_conclusao(client, headers, tentativas: int = 200) -> dict:
    """
    A ingestão roda em background; o teste acompanha pela mesma rota que o painel.

    O TestClient roda o event loop num thread próprio, então basta consultar até
    o job sair de `running`.
    """
    for _ in range(tentativas):
        job = client.get("/api/admin/rag/ingest", headers=headers).json()
        if job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("A ingestão não terminou no tempo esperado.")


def test_ingestao_indexa_o_diretorio_e_registra_o_documento(client, monkeypatch):
    _documento(client)
    chamadas = _duplo_de_ingestao(client, monkeypatch)
    headers = _token(client)

    inicio = client.post("/api/admin/rag/ingest", headers=headers)
    # 202: a rota inicia o trabalho, não espera terminar — indexar a base passa
    # do proxy_read_timeout do nginx e devolvia 504 com a ingestão em curso.
    assert inicio.status_code == 202
    assert inicio.json()["state"] == "running"
    assert inicio.json()["files"] == ["aws-sustainability-2025.txt"]

    job = _aguarda_conclusao(client, headers)
    assert job["state"] == "done"
    assert job["result"]["files_processed"] == 1
    assert job["result"]["chunks"] == 7
    assert job["progress"] == {"done": 1, "total": 1, "current": None}
    assert len(chamadas[0]) == 1

    depois = client.get("/api/admin/rag/status", headers=headers).json()
    assert depois["pending_files"] == 0
    assert depois["files"][0]["indexed"] is True
    assert depois["files"][0]["chunks"] == 7
    assert depois["documents_indexed"] == 1
    assert depois["job"]["state"] == "done"


def test_ingestao_processa_um_arquivo_por_vez(client, monkeypatch):
    """
    Arquivo a arquivo: é o que torna o progresso real e o que mantém indexado o
    que já terminou, caso o processo caia no meio.
    """
    _documento(client, nome="aws-2025.txt")
    _documento(client, nome="azure-2025.txt")
    chamadas = _duplo_de_ingestao(client, monkeypatch)
    headers = _token(client)

    client.post("/api/admin/rag/ingest", headers=headers)
    job = _aguarda_conclusao(client, headers)

    assert [len(lote) for lote in chamadas] == [1, 1]
    assert job["result"]["files_processed"] == 2
    assert job["result"]["chunks"] == 14


def test_ingestao_ja_em_andamento_recusa_um_segundo_disparo(client, monkeypatch):
    _documento(client)
    headers = _token(client)

    liberado = threading.Event()

    def ingest_lento(paths, scope, session_id=None, source_type=None, guardrail_log=None):
        liberado.wait(timeout=5)
        return {
            "chunks": 1,
            "files_processed": 1,
            "files_failed": 0,
            "details": [],
            "unassigned_files": [],
            "documents": [],
            "errors": [],
        }

    monkeypatch.setattr(client.documents.rag, "ingest_paths", ingest_lento)

    assert client.post("/api/admin/rag/ingest", headers=headers).status_code == 202
    segunda = client.post("/api/admin/rag/ingest", headers=headers)
    assert segunda.status_code == 409

    liberado.set()
    _aguarda_conclusao(client, headers)


def test_falha_no_pipeline_deixa_o_job_em_erro(client, monkeypatch):
    _documento(client)
    headers = _token(client)

    def explode(paths, scope, session_id=None, source_type=None, guardrail_log=None):
        raise RuntimeError("índice corrompido")

    monkeypatch.setattr(client.documents.rag, "ingest_paths", explode)

    client.post("/api/admin/rag/ingest", headers=headers)
    job = _aguarda_conclusao(client, headers)
    assert job["state"] == "error"
    assert "índice corrompido" in job["error"]


def test_ingestao_aceita_selecao_de_arquivos(client, monkeypatch):
    _documento(client, nome="aws-2025.txt")
    _documento(client, nome="azure-2025.txt")
    chamadas = _duplo_de_ingestao(client, monkeypatch)
    headers = _token(client)

    resposta = client.post(
        "/api/admin/rag/ingest", headers=headers, json={"files": ["azure-2025.txt"]}
    )
    assert resposta.status_code == 202
    _aguarda_conclusao(client, headers)
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
    assert corpo["state"] == "idle"
    assert corpo["result"] is None
    assert "data/pdf" in corpo["message"]


# --- Limpeza da base vetorial ----------------------------------------------


def _indice_falso(client, trechos: int = 7, monkeypatch=None):
    """
    Escreve os dois arquivos que o FAISS persiste e finge a contagem.

    O conteúdo não é um índice de verdade — o que estes testes verificam é que a
    limpeza apaga os arquivos e o registro, não que o FAISS saiba lê-los.
    """
    import config

    caminho = Path(config.get_settings().vector_db_path)
    caminho.mkdir(parents=True, exist_ok=True)
    (caminho / "index.faiss").write_bytes(b"faiss")
    (caminho / "index.pkl").write_bytes(b"pickle")
    if monkeypatch is not None:
        monkeypatch.setattr(client.documents.rag, "count_chunks", lambda: trechos)
    return caminho


def test_limpeza_exige_token(client):
    assert client.post("/api/admin/rag/reset").status_code == 401


def test_limpeza_apaga_o_indice_e_desregistra_os_documentos(client, monkeypatch):
    _documento(client)
    _duplo_de_ingestao(client, monkeypatch)
    headers = _token(client)
    client.post("/api/admin/rag/ingest", headers=headers)
    _aguarda_conclusao(client, headers)
    caminho = _indice_falso(client, trechos=7, monkeypatch=monkeypatch)

    corpo = client.post("/api/admin/rag/reset", headers=headers).json()
    assert corpo == {"index_removed": True, "documents_cleared": 1, "chunks_removed": 7}
    assert not (caminho / "index.faiss").exists()
    assert not (caminho / "index.pkl").exists()

    depois = client.get("/api/admin/rag/status", headers=headers).json()
    assert depois["index_ready"] is False
    assert depois["documents_indexed"] == 0
    # O arquivo continua em data/pdf e volta a constar como pendente: o que foi
    # apagado é o índice, não a fonte.
    assert depois["pending_files"] == 1
    assert depois["files"][0]["indexed"] is False


def test_indice_ilegivel_ainda_pode_ser_limpo(client, monkeypatch):
    """
    O caso que motiva a limpeza: trocado o modelo de embedding, é justamente a
    leitura do índice que falha. Não conseguir contar não pode impedir apagar.
    """
    caminho = _indice_falso(client)

    def explode():
        raise RuntimeError("dimensão do vetor não confere")

    monkeypatch.setattr(client.documents.rag, "count_chunks", explode)

    corpo = client.post("/api/admin/rag/reset", headers=_token(client)).json()
    assert corpo["index_removed"] is True
    assert corpo["chunks_removed"] is None
    assert not (caminho / "index.faiss").exists()


def test_limpeza_sem_indice_nao_e_erro(client):
    corpo = client.post("/api/admin/rag/reset", headers=_token(client)).json()
    assert corpo == {"index_removed": False, "documents_cleared": 0, "chunks_removed": 0}


def test_limpeza_durante_a_ingestao_e_recusada(client, monkeypatch):
    """Apagar o índice sob os pés de quem está escrevendo nele deixaria os dois inconsistentes."""
    _documento(client)
    headers = _token(client)
    liberado = threading.Event()

    def ingest_lento(paths, scope, session_id=None, source_type=None, guardrail_log=None):
        liberado.wait(timeout=5)
        return {
            "chunks": 1,
            "files_processed": 1,
            "files_failed": 0,
            "details": [],
            "unassigned_files": [],
            "documents": [],
            "errors": [],
        }

    monkeypatch.setattr(client.documents.rag, "ingest_paths", ingest_lento)
    assert client.post("/api/admin/rag/ingest", headers=headers).status_code == 202

    recusa = client.post("/api/admin/rag/reset", headers=headers)
    assert recusa.status_code == 409

    liberado.set()
    _aguarda_conclusao(client, headers)


# --- Divergência de modelo de embedding ------------------------------------


def test_modelo_de_embedding_trocado_e_sinalizado_no_inventario(client, monkeypatch):
    """
    A falha que o inventário existe para tornar visível: o índice responde às
    buscas com os vetores do modelo que o gerou, e trocar o modelo faz toda
    consulta voltar vazia sem erro nenhum na tela.
    """
    import config

    # O modelo em vigor na ingestão é fixado aqui: o registro do documento grava
    # o que estiver configurado no momento, e o teste precisa saber qual foi.
    monkeypatch.setenv("EMBEDDING_PROVIDER", "huggingface")
    monkeypatch.setenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    config.reload_settings()

    _documento(client)
    _duplo_de_ingestao(client, monkeypatch)
    headers = _token(client)
    client.post("/api/admin/rag/ingest", headers=headers)
    _aguarda_conclusao(client, headers)

    antes = client.get("/api/admin/rag/status", headers=headers).json()
    assert antes["embedding_mismatch"] is False
    assert antes["index_embedding_model"] == "all-MiniLM-L6-v2"

    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-ada-002")
    config.reload_settings()

    depois = client.get("/api/admin/rag/status", headers=headers).json()
    assert depois["embedding_mismatch"] is True
    assert depois["embedding_model"] == "text-embedding-ada-002"
    # O modelo que gerou o índice continua sendo o que está no registro.
    assert depois["index_embedding_model"] == "all-MiniLM-L6-v2"


def test_sem_documento_registrado_nao_ha_divergencia(client):
    """Índice vazio não tem modelo: alarme sem base seria ruído no painel."""
    corpo = client.get("/api/admin/rag/status", headers=_token(client)).json()
    assert corpo["embedding_mismatch"] is False
    assert corpo["index_embedding_model"] is None


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
