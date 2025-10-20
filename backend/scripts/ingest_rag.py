import os
from dotenv import load_dotenv
from langchain.embeddings import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores import Chroma
from langchain.document_loaders import PyPDFLoader, WebBaseLoader

# Carregar variáveis de ambiente
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", "./chroma_db")
VECTOR_COLLECTION_NAME = os.getenv("VECTOR_COLLECTION_NAME", "providers_docs")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 1000))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 200))


def ingest_pdfs(pdf_paths):
    """Carrega e divide PDFs em chunks e indexa no Chroma."""
    embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

    docs_total = []
    for path in pdf_paths:
        loader = PyPDFLoader(path)
        docs = loader.load()
        chunks = splitter.split_documents(docs)
        print(f"[PDF] {path}: {len(chunks)} chunks gerados.")
        docs_total.extend(chunks)

    db = Chroma.from_documents(
        documents=docs_total,
        embedding=embeddings,
        persist_directory=VECTOR_DB_PATH,
        collection_name=VECTOR_COLLECTION_NAME
    )
    db.persist()
    print(f"✅ Indexação concluída com {len(docs_total)} chunks.")
    return db


def ingest_webpages(urls):
    """Carrega e indexa conteúdos web."""
    embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)

    docs_total = []
    for url in urls:
        loader = WebBaseLoader(url)
        docs = loader.load()
        chunks = splitter.split_documents(docs)
        print(f"[WEB] {url}: {len(chunks)} chunks gerados.")
        docs_total.extend(chunks)

    db = Chroma.from_documents(
        documents=docs_total,
        embedding=embeddings,
        persist_directory=VECTOR_DB_PATH,
        collection_name=VECTOR_COLLECTION_NAME
    )
    db.persist()
    print(f"✅ Indexação concluída com {len(docs_total)} chunks.")
    return db


if __name__ == "__main__":
    # Exemplo de ingestão combinada
    pdfs = [
        "./data/2023-amazon-sustainability-report-aws-summary.pdf",
        "./data/2025-azure-environmental-sustainability-report.pdf",
        "./data/2023-google-environmental-report.pdf"
    ]

    urls = [
        "https://aws.amazon.com/pt/sustainability/",
        "https://sustainability.google/solutions/cloud/",
        "https://azure.microsoft.com/en-us/global-infrastructure/sustainability"
    ]

    print("🔄 Iniciando ingestão de PDFs e páginas web...")
    db_pdf = ingest_pdfs(pdfs)
    # db_web = ingest_webpages(urls)
    print("🏁 Processo concluído.")
