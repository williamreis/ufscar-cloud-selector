"""
Ingestão dos documentos globais pela linha de comando (`make ingest`).

Faz exatamente o que o endpoint `/api/documents/ingest-global` faz, pelo mesmo
caminho de código: validação de arquivo, chunking, metadados completos (§14.3) e
gravação no índice FAISS, mais o registro dos documentos no banco de auditoria.

A versão anterior deste script montava o índice por conta própria, com
`OpenAIEmbeddings` fixo e sem metadado nenhum. Depois da Fase 0 isso passou a ser
ativamente nocivo: os trechos gravados por aqui entrariam no mesmo índice **sem
`chunk_id`**, e portanto sem como sustentar a proveniência que a §19 exige.

Uso:

    python scripts/ingest_rag.py                 # tudo que estiver em data/pdf
    python scripts/ingest_rag.py a.pdf b.txt     # apenas os arquivos indicados
"""

import os
import sys
from pathlib import Path

# Os módulos da aplicação se importam de forma plana (`from config import ...`),
# porque o uvicorn roda com working_dir em backend/app. Reproduzimos esse sys.path
# em vez de reescrever os imports da aplicação.
APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import db  # noqa: E402
import rag  # noqa: E402
from config import get_settings  # noqa: E402
from guardrails import GuardrailLog  # noqa: E402
from rag.metadata import SCOPE_GLOBAL  # noqa: E402


def collect_paths(argv: list) -> list:
    settings = get_settings()
    if argv:
        return [str(Path(a).resolve()) for a in argv]

    pdf_dir = Path(os.getenv("PDF_DIR", APP_DIR.parent / "data" / "pdf")).resolve()
    if not pdf_dir.is_dir():
        return []
    return [
        str(p)
        for p in sorted(pdf_dir.iterdir())
        if p.is_file() and p.suffix.lower() in settings.allowed_upload_extensions
    ]


def main(argv: list) -> int:
    settings = get_settings()
    paths = collect_paths(argv)
    if not paths:
        print("Nenhum arquivo para ingerir. Coloque PDFs ou TXTs em data/pdf.")
        return 1

    print(f"Embeddings: {settings.embedding_provider}/{settings.embedding_model}")
    print(f"{len(paths)} arquivo(s) a processar.\n")

    guardrail_log = GuardrailLog()
    result = rag.ingest_paths(paths, scope=SCOPE_GLOBAL, guardrail_log=guardrail_log)

    for detail in result["details"]:
        provider = detail["provider"] or "— sem provedor identificado no nome —"
        year = detail["year"] or "sem ano"
        print(f"  {detail['file_name']}: {detail['chunks']} chunks · {provider} · {year}")

    db.init_db()
    registered = db.save_documents(result["details"])

    print(f"\n{result['chunks']} chunks indexados · {registered} documento(s) registrado(s).")

    if result["unassigned_files"]:
        print(
            "\nSem provedor identificável no nome (serão indexados, mas não viram "
            "evidência de ninguém):"
        )
        for name in result["unassigned_files"]:
            print(f"  - {name}")

    for event in guardrail_log.events:
        print(f"\n[guardrail] {event.rule_id} ({event.action}) em {event.target}: {event.reason}")

    for error in result["errors"]:
        print(f"\n[erro] {error}")

    return 0 if result["chunks"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
