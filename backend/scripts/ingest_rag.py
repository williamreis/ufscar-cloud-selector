"""
Ingestão dos documentos globais pela linha de comando (`make ingest`).

Faz exatamente o que o endpoint `/api/documents/ingest-global` faz, pelo mesmo
caminho de código: validação de arquivo, chunking, metadados completos (§14.3) e
gravação no índice FAISS, mais o registro dos documentos no banco de auditoria.

A versão anterior deste script montava o índice por conta própria, com
`OpenAIEmbeddings` fixo e sem metadado nenhum. Depois da Fase 0 isso passou a ser
ativamente nocivo: os trechos gravados por aqui entrariam no mesmo índice **sem
`chunk_id`**, e portanto sem como sustentar a proveniência que a §19 exige.

**Sobre reindexar.** O índice FAISS não é idempotente: `rag.ingest_paths`
acrescenta vetores ao que já existe e não remove os da ingestão anterior do mesmo
documento. Rodar a ingestão completa duas vezes deixa cada trecho duplicado no
índice — e o efeito não é só desperdício de espaço. A recuperação é por
similaridade com `top_k` pequeno, então as cópias competem pelas mesmas vagas: um
`top_k` de 3 passa a entregar dois trechos distintos, e a evidência que estava na
terceira posição some do contexto da LLM. O indicador vira `NOT_FOUND`, sai do
conjunto comparável (§11.1) e o ranking perde base — tudo isso sem erro nenhum
aparecendo em lugar algum.

Por isso a ingestão **completa** limpa o índice antes, por padrão. Ingerir
arquivos avulsos (com nomes na linha de comando) continua sendo acréscimo, que é
o que se espera de quem manda um arquivo por vez.

Uso:

    python scripts/ingest_rag.py                 # tudo de data/pdf, reconstruindo o índice
    python scripts/ingest_rag.py --sem-reset     # acrescenta (pode duplicar; ver acima)
    python scripts/ingest_rag.py a.pdf b.txt     # apenas os indicados, sempre acrescentando
"""

import argparse
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
    parser = argparse.ArgumentParser(description="Ingestão dos documentos globais.")
    parser.add_argument(
        "--sem-reset",
        action="store_true",
        help="acrescenta ao índice existente em vez de reconstruí-lo (pode duplicar vetores)",
    )
    parser.add_argument("arquivos", nargs="*", help="arquivos específicos (padrão: tudo de data/pdf)")
    args = parser.parse_args(argv)

    settings = get_settings()
    paths = collect_paths(args.arquivos)
    if not paths:
        print("Nenhum arquivo para ingerir. Coloque PDFs ou TXTs em data/pdf.")
        return 1

    print(f"Embeddings: {settings.embedding_provider}/{settings.embedding_model}")
    print(f"{len(paths)} arquivo(s) a processar.\n")

    # Reconstruir só faz sentido quando a ingestão é do acervo inteiro: apagar o
    # índice para depois acrescentar um arquivo avulso descartaria todo o resto.
    ingestao_completa = not args.arquivos
    if ingestao_completa and not args.sem_reset:
        anteriores = rag.count_chunks()
        if rag.delete_index():
            db.init_db()
            db.clear_documents(scope=SCOPE_GLOBAL)
            print(f"Índice anterior removido ({anteriores} trechos) — reconstruindo do zero.\n")
    elif ingestao_completa:
        anteriores = rag.count_chunks()
        if anteriores:
            print(
                f"AVISO: acrescentando ao índice existente ({anteriores} trechos). "
                "Trechos já indexados serão duplicados e disputarão as vagas do top_k.\n"
            )

    guardrail_log = GuardrailLog()
    result = rag.ingest_paths(paths, scope=SCOPE_GLOBAL, guardrail_log=guardrail_log)

    for detail in result["details"]:
        provider = detail["provider"] or "— sem provedor identificado no nome —"
        year = detail["year"] or "sem ano"
        print(f"  {detail['file_name']}: {detail['chunks']} chunks · {provider} · {year}")

    db.init_db()
    registered = db.save_documents(result["details"])

    print(f"\n{result['chunks']} chunks indexados · {registered} documento(s) registrado(s).")

    # Conferência de fechamento. Numa reconstrução, o índice tem de conter
    # exatamente o que acabou de ser indexado; num acréscimo, o que havia mais o
    # que entrou. Divergência para mais significa cópia duplicada — o modo de
    # falha que não levanta erro nenhum e só aparece semanas depois, como
    # indicador que sumiu do ranking.
    no_indice = rag.count_chunks()
    esperado = result["chunks"] if ingestao_completa and not args.sem_reset else None
    if esperado is not None and no_indice != esperado:
        print(
            f"\nAVISO: o índice tem {no_indice} trechos, mas esta ingestão gravou "
            f"{esperado}. A diferença ({no_indice - esperado}) indica vetores "
            "duplicados — refaça com o índice limpo."
        )

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
