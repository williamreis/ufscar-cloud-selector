"""
Versionamento do questionário e do algoritmo (diretriz §28).

A promessa que estes testes protegem: um resultado gravado hoje continua
interpretável depois de o `questions.json` ser editado, porque a avaliação aponta
para a versão que estava valendo. O `questions.json` é montado como volume
justamente para poder mudar sem rebuild — o hash é o que impede que essa
flexibilidade apague o passado.
"""

import json

from audit import canonical_hash, questionnaire_fingerprint, runtime_versions
from config import reload_settings

QUESTIONARIO = {
    "option_sets": {"relevance": ["Decisivo", "Relevante"]},
    "sections": [
        {"id": "a", "questions": [{"id": "q1", "label": "Relevância da eficiência energética?"}]},
        {"id": "b", "questions": [{"id": "q2"}, {"id": "q3"}]},
    ],
}


def _escrever(tmp_path, payload, indent=2):
    caminho = tmp_path / "questions.json"
    caminho.write_text(json.dumps(payload, indent=indent, ensure_ascii=False), encoding="utf-8")
    return caminho


# --- Hash canônico ---------------------------------------------------------


def test_hash_ignora_formatacao(tmp_path):
    """Reindentar o arquivo não pode invalidar os registros já gravados."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    um = _escrever(tmp_path / "a", QUESTIONARIO, indent=None)
    outro = _escrever(tmp_path / "b", QUESTIONARIO, indent=4)

    assert questionnaire_fingerprint(um)["questions_hash"] == (
        questionnaire_fingerprint(outro)["questions_hash"]
    )


def test_hash_ignora_ordem_das_chaves():
    invertido = {"sections": QUESTIONARIO["sections"], "option_sets": QUESTIONARIO["option_sets"]}
    assert canonical_hash(QUESTIONARIO) == canonical_hash(invertido)


def test_hash_muda_quando_o_enunciado_muda(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    original = _escrever(tmp_path / "a", QUESTIONARIO)

    alterado = json.loads(json.dumps(QUESTIONARIO))
    alterado["sections"][0]["questions"][0]["label"] = "Outro enunciado?"
    modificado = _escrever(tmp_path / "b", alterado)

    assert questionnaire_fingerprint(original)["questions_hash"] != (
        questionnaire_fingerprint(modificado)["questions_hash"]
    )


def test_hash_muda_quando_uma_pergunta_e_removida(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    original = _escrever(tmp_path / "a", QUESTIONARIO)

    reduzido = json.loads(json.dumps(QUESTIONARIO))
    reduzido["sections"][1]["questions"].pop()
    menor = _escrever(tmp_path / "b", reduzido)

    assert questionnaire_fingerprint(original)["questions_hash"] != (
        questionnaire_fingerprint(menor)["questions_hash"]
    )


def test_contagem_de_perguntas(tmp_path):
    assert questionnaire_fingerprint(_escrever(tmp_path, QUESTIONARIO))["question_count"] == 3


def test_reler_o_arquivo_alterado_produz_hash_novo(tmp_path):
    """O volume é editável em tempo de execução; o cache não pode congelar o hash."""
    caminho = _escrever(tmp_path, QUESTIONARIO)
    antes = questionnaire_fingerprint(caminho)["questions_hash"]

    alterado = json.loads(json.dumps(QUESTIONARIO))
    alterado["sections"][0]["questions"][0]["id"] = "q1_novo"
    caminho.write_text(json.dumps(alterado, indent=2), encoding="utf-8")

    assert questionnaire_fingerprint(caminho)["questions_hash"] != antes


# --- Ausência do arquivo ---------------------------------------------------


def test_arquivo_ausente_nao_derruba_e_deixa_a_lacuna_explicita(tmp_path):
    resultado = questionnaire_fingerprint(tmp_path / "nao-existe.json")
    assert resultado["questions_hash"] is None
    assert resultado["unavailable_reason"]


def test_arquivo_ilegivel_nao_inventa_hash(tmp_path):
    caminho = tmp_path / "questions.json"
    caminho.write_text("{ isto não é json", encoding="utf-8")
    resultado = questionnaire_fingerprint(caminho)
    assert resultado["questions_hash"] is None
    assert "ilegível" in resultado["unavailable_reason"]


# --- Bloco de versões da execução ------------------------------------------


def test_bloco_de_versoes_identifica_a_execucao(monkeypatch):
    monkeypatch.setenv("SCORING_ALGORITHM_VERSION", "7")
    monkeypatch.setenv("QUESTIONNAIRE_VERSION", "2")
    reload_settings()
    try:
        versoes = runtime_versions(prompt_versions={"PROMPT_X_V1": "1"})
        assert versoes["algorithm_version"] == "7"
        assert versoes["questionnaire_version"] == "2"
        assert versoes["prompt_versions"] == {"PROMPT_X_V1": "1"}
        # §27 exige provedor e modelo de LLM **e** de embedding.
        for chave in ("llm_provider", "llm_model", "embedding_provider", "embedding_model"):
            assert versoes[chave]
    finally:
        monkeypatch.delenv("SCORING_ALGORITHM_VERSION", raising=False)
        monkeypatch.delenv("QUESTIONNAIRE_VERSION", raising=False)
        reload_settings()
