"""
Validação de arquivos enviados (diretriz §23.3).

O que estes testes protegem é a diferença entre checar a extensão e checar o
arquivo. Antes da Fase 0 bastava terminar em `.pdf`; renomear um executável
passava. Aqui o que decide é a assinatura real do conteúdo, e a extensão é só o
primeiro filtro.
"""

import pytest

from config import reload_settings
from guardrails import GuardrailLog, GuardrailRejection, safe_filename, validate_upload
from guardrails.files import enforce_document_quota, resolve_within

PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n"
TXT = "PUE de 1,12 em 2025 — relatório do provedor.".encode("utf-8")


def _rule(exc: GuardrailRejection) -> str:
    return exc.event.rule_id


# --- O que passa -----------------------------------------------------------


def test_pdf_valido_e_aceito():
    result = validate_upload("relatorio-aws-2025.pdf", PDF)
    assert result.detected_type == "pdf"
    assert result.extension == ".pdf"
    assert result.size == len(PDF)


def test_txt_valido_e_aceito():
    result = validate_upload("notas.txt", TXT)
    assert result.detected_type == "text"


def test_txt_em_latin1_ainda_e_texto():
    """Documento antigo em Latin-1 não deve ser confundido com binário."""
    result = validate_upload("notas.txt", "acentuação".encode("latin-1"))
    assert result.detected_type == "text"


# --- O que não passa -------------------------------------------------------


def test_extensao_nao_permitida():
    with pytest.raises(GuardrailRejection) as exc:
        validate_upload("planilha.xlsx", b"PK\x03\x04qualquer")
    assert _rule(exc.value) == "FILE_EXTENSION_NOT_ALLOWED"


def test_executavel_renomeado_para_pdf_nao_passa():
    """O caso que a checagem por extensão deixava entrar."""
    with pytest.raises(GuardrailRejection) as exc:
        validate_upload("relatorio.pdf", b"MZ\x90\x00" + b"\x00" * 100)
    assert _rule(exc.value) == "FILE_EXECUTABLE_CONTENT"


def test_elf_renomeado_para_txt_nao_passa():
    with pytest.raises(GuardrailRejection) as exc:
        validate_upload("leiame.txt", b"\x7fELF\x02\x01\x01\x00")
    assert _rule(exc.value) == "FILE_EXECUTABLE_CONTENT"


def test_script_com_shebang_nao_passa():
    with pytest.raises(GuardrailRejection) as exc:
        validate_upload("notas.txt", b"#!/bin/sh\nrm -rf /\n")
    assert _rule(exc.value) == "FILE_EXECUTABLE_CONTENT"


def test_txt_com_conteudo_binario_nao_passa():
    with pytest.raises(GuardrailRejection) as exc:
        validate_upload("notas.txt", b"texto\x00\x01\x02binario")
    assert _rule(exc.value) == "FILE_TYPE_UNRECOGNIZED"


def test_extensao_pdf_com_conteudo_de_texto_nao_passa():
    """Renomear não muda o que o arquivo é — nos dois sentidos."""
    with pytest.raises(GuardrailRejection) as exc:
        validate_upload("relatorio.pdf", TXT)
    assert _rule(exc.value) == "FILE_MIME_MISMATCH"


def test_arquivo_vazio_nao_passa():
    with pytest.raises(GuardrailRejection) as exc:
        validate_upload("vazio.pdf", b"")
    assert _rule(exc.value) == "FILE_EMPTY"


def test_limite_de_tamanho_vem_da_configuracao(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    reload_settings()
    try:
        with pytest.raises(GuardrailRejection) as exc:
            validate_upload("grande.pdf", PDF + b"\x20" * (2 * 1024 * 1024))
        assert _rule(exc.value) == "FILE_TOO_LARGE"
        assert "MAX_UPLOAD_MB" in exc.value.event.reason
    finally:
        monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)
        reload_settings()


# --- Nome e caminho --------------------------------------------------------


def test_nome_gravado_e_saneado():
    stored = safe_filename("../../etc/relatório da AWS (2025).pdf")
    assert "/" not in stored and ".." not in stored
    assert stored.endswith(".pdf")
    # Acento e espaço não sobrevivem; o nome continua reconhecível.
    assert "relatorio" in stored and "AWS" in stored


def test_nomes_iguais_geram_arquivos_distintos():
    assert safe_filename("a.pdf") != safe_filename("a.pdf")


def test_arquivo_dentro_do_diretorio_e_resolvido(tmp_path):
    (tmp_path / "dentro.pdf").write_bytes(PDF)
    assert resolve_within(tmp_path, "dentro.pdf").is_file()


@pytest.mark.parametrize("hostil", ["../dentro.pdf", "../../etc/passwd", "/etc/passwd"])
def test_travessia_e_desarmada_para_o_nome_final(tmp_path, hostil):
    """
    `../` e caminho absoluto não escapam porque só o nome final é aproveitado —
    o resultado aponta para dentro do diretório permitido, seja qual for a entrada.
    """
    resolved = resolve_within(tmp_path, hostil)
    assert resolved.parent == tmp_path.resolve()


def test_symlink_apontando_para_fora_e_bloqueado(tmp_path):
    """
    O caso que a extração do nome final **não** cobre: o arquivo existe dentro do
    diretório, mas é um link para fora dele.
    """
    fora = tmp_path / "fora"
    fora.mkdir()
    alvo = fora / "segredo.pdf"
    alvo.write_bytes(PDF)

    permitido = tmp_path / "permitido"
    permitido.mkdir()
    (permitido / "inocente.pdf").symlink_to(alvo)

    with pytest.raises(GuardrailRejection) as exc:
        resolve_within(permitido, "inocente.pdf")
    assert _rule(exc.value) == "PATH_TRAVERSAL_BLOCKED"


# --- Quota e registro ------------------------------------------------------


def test_quota_de_documentos_por_avaliacao(monkeypatch):
    monkeypatch.setenv("MAX_DOCUMENTS_PER_EVALUATION", "3")
    reload_settings()
    try:
        enforce_document_quota(2, 1, target="sessao")  # exatamente no limite
        with pytest.raises(GuardrailRejection) as exc:
            enforce_document_quota(2, 2, target="sessao")
        assert exc.value.event.rule_id == "DOCUMENT_QUOTA_EXCEEDED"
    finally:
        monkeypatch.delenv("MAX_DOCUMENTS_PER_EVALUATION", raising=False)
        reload_settings()


def test_recusa_fica_registrada_no_log():
    log = GuardrailLog()
    with pytest.raises(GuardrailRejection):
        validate_upload("virus.pdf", b"MZ\x90\x00")
    assert len(log) == 0  # sem log passado, nada é registrado

    log = GuardrailLog()
    with pytest.raises(GuardrailRejection):
        validate_upload("virus.pdf", b"MZ\x90\x00", log)
    assert [e.rule_id for e in log.events] == ["FILE_EXECUTABLE_CONTENT"]
    assert log.events[0].action == "REJECT"
