"""
Versionamento do questionário, dos prompts e do algoritmo (diretriz §28).

Cada avaliação precisa apontar para *o que estava valendo* quando ela rodou. Sem
isso, um resultado gravado hoje deixa de ser interpretável assim que o
`questions.json` for editado ou a regra de pontuação mudar — e o `questions.json`
é montado como volume justamente para poder ser alterado sem rebuild.

O hash é calculado sobre a forma **canônica** do JSON (chaves ordenadas, sem
espaço supérfluo), não sobre os bytes do arquivo. Reindentar o arquivo não muda
o hash; mudar um enunciado, uma opção ou a ordem das perguntas muda.
"""

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from config import get_settings

logger = logging.getLogger("uvicorn.error")

# Cache chaveado por (caminho, mtime, tamanho): o arquivo é um volume editável em
# tempo de execução, então relê quando ele muda, sem exigir restart do backend.
_hash_cache: Dict[Tuple[str, float, int], Dict[str, Any]] = {}


def canonical_hash(payload: Any) -> str:
    """SHA-256 da forma canônica de uma estrutura JSON."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _count_questions(payload: Any) -> Optional[int]:
    """Número de perguntas do questionário, quando o formato é reconhecível."""
    if not isinstance(payload, dict):
        return None
    sections = payload.get("sections")
    if not isinstance(sections, list):
        return None
    total = 0
    for section in sections:
        questions = (section or {}).get("questions") if isinstance(section, dict) else None
        if isinstance(questions, list):
            total += len(questions)
    return total


def questionnaire_fingerprint(path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Identificação da versão do questionário em vigor.

    Um `questions.json` ausente ou ilegível **não derruba a requisição**: o hash
    volta nulo e o motivo fica registrado no próprio retorno. Perder a
    rastreabilidade da versão é ruim; recusar a avaliação inteira por causa de um
    volume não montado seria pior, e o campo nulo deixa a lacuna explícita em vez
    de fingir uma versão que não foi conferida.
    """
    settings = get_settings()
    target = Path(path) if path is not None else settings.questions_json_path

    base: Dict[str, Any] = {
        "questionnaire_version": settings.questionnaire_version,
        "questions_hash": None,
        "questions_source": str(target),
        "question_count": None,
        "unavailable_reason": None,
    }

    try:
        stat = target.stat()
    except OSError as exc:
        logger.warning("questions.json indisponível para versionamento (%s): %s", target, exc)
        return {**base, "unavailable_reason": f"arquivo indisponível: {exc.strerror or exc}"}

    key = (str(target), stat.st_mtime, stat.st_size)
    cached = _hash_cache.get(key)
    if cached is not None:
        return {**base, **cached}

    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("questions.json ilegível para versionamento (%s): %s", target, exc)
        return {**base, "unavailable_reason": f"conteúdo ilegível: {exc}"}

    resolved = {
        "questions_hash": canonical_hash(payload),
        "question_count": _count_questions(payload),
        "unavailable_reason": None,
    }
    _hash_cache.clear()  # só interessa a versão corrente do arquivo
    _hash_cache[key] = resolved
    return {**base, **resolved}


def runtime_versions(prompt_versions: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Bloco de versões a gravar junto de cada avaliação (§28 e §32.2).

    Reúne o que identifica a execução: questionário, algoritmo de pontuação,
    provedor/modelo de LLM, provedor/modelo de embedding e os prompts usados.
    """
    settings = get_settings()
    versions: Dict[str, Any] = {
        **questionnaire_fingerprint(),
        "algorithm_version": settings.scoring_algorithm_version,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
    }
    if prompt_versions:
        versions["prompt_versions"] = dict(prompt_versions)
    return versions
