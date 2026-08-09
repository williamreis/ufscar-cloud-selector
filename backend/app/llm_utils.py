import os
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

load_dotenv()

# ===============================
# Configurações e variáveis
# ===============================
# Provedores suportados: openai | groq | openrouter | gemini | ollama
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# llama-3.1-70b-versatile foi descomissionado pela Groq; openai/gpt-oss-120b é o
# modelo gratuito recomendado atualmente (console.groq.com/docs/deprecations)
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# OpenRouter: um único endpoint compatível com a API da OpenAI dando acesso a
# dezenas de modelos gratuitos (":free"). Lista/limites mudam com frequência,
# conferir sempre em https://openrouter.ai/models?max_price=0
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-4-maverick:free")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Ollama: inferência local, 100% gratuita, mas exige um servidor Ollama rodando
# (self-hosted) apontado por OLLAMA_BASE_URL.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")

# Caminho relativo à raiz do processo (backend/app/): "../data/..." aponta para
# backend/data/..., que é o diretório montado como volume no docker-compose.
# Usar "./data/..." aqui faria o índice ser gravado fora do volume persistente.
VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", "../data/faiss_index")
VECTOR_COLLECTION_NAME = os.getenv("VECTOR_COLLECTION_NAME", "providers_docs")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "1000"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "200"))

# Metadados no índice FAISS: source="global" (data/pdf) ou source="session" + session_id (data/upload/<session_id>)
METADATA_SOURCE_GLOBAL = "global"
METADATA_SOURCE_SESSION = "session"

# Termos de busca por critério (indicadores do AHP). Usados para que cada evidência
# recuperada esteja ligada a um indicador específico, e não a uma consulta genérica.
CRITERIA_QUERIES = {
    "sustainability": (
        "eficiência energética do data center, energia renovável, emissões de carbono, "
        "PUE, water usage, metas de sustentabilidade"
    ),
    "performance": (
        "disponibilidade uptime SLA, latência, desempenho, escalabilidade, "
        "capacidade de computação, confiabilidade da infraestrutura"
    ),
    "security": (
        "segurança da informação, certificações ISO 27001 SOC 2 GDPR, criptografia, "
        "backup e recuperação de desastres, conformidade e auditoria"
    ),
}


# ===============================
# Escolha dinâmica do provedor LLM
# ===============================
def get_llm():
    """
    Inicializa dinamicamente o LLM conforme LLM_PROVIDER (.env):
    openai | groq | openrouter | gemini | ollama.
    """
    if LLM_PROVIDER == "groq":
        from langchain_groq import ChatGroq
        print(f"🔹 Usando Groq (free tier) com modelo: {GROQ_MODEL}")
        return ChatGroq(
            groq_api_key=GROQ_API_KEY,
            model_name=GROQ_MODEL,
            temperature=0.2,
            max_tokens=1500,
        )

    if LLM_PROVIDER == "openrouter":
        print(f"🔹 Usando OpenRouter (modelos :free) com modelo: {OPENROUTER_MODEL}")
        return ChatOpenAI(
            openai_api_key=OPENROUTER_API_KEY,
            openai_api_base="https://openrouter.ai/api/v1",
            model_name=OPENROUTER_MODEL,
            temperature=0.2,
            max_tokens=1500,
        )

    if LLM_PROVIDER == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        print(f"🔹 Usando Gemini (free tier) com modelo: {GEMINI_MODEL}")
        return ChatGoogleGenerativeAI(
            google_api_key=GEMINI_API_KEY,
            model=GEMINI_MODEL,
            temperature=0.2,
            max_output_tokens=1500,
        )

    if LLM_PROVIDER == "ollama":
        from langchain_ollama import ChatOllama
        print(f"🔹 Usando Ollama local com modelo: {OLLAMA_MODEL}")
        return ChatOllama(
            base_url=OLLAMA_BASE_URL,
            model=OLLAMA_MODEL,
            temperature=0.2,
        )

    print(f"🔹 Usando OpenAI API com modelo: {OPENAI_MODEL}")
    return ChatOpenAI(
        openai_api_key=OPENAI_API_KEY,
        model_name=OPENAI_MODEL,
        temperature=0.2,
        max_tokens=1500,
    )


# ===============================
# Prompt para análise de pesos AHP
# ===============================
WEIGHTS_PROMPT = PromptTemplate(
    input_variables=["qa_pairs", "numeric_scores", "comparative_adjustments"],
    template="""
Você é um assistente especialista em Cloud Computing e decisão multicritério (AHP).
O gestor respondeu um questionário sobre sustentabilidade, desempenho e segurança de provedores de nuvem.

Perguntas e respostas do gestor (na íntegra):
{qa_pairs}

Intensidade já calculada pelas perguntas fechadas (escala 1–5): {numeric_scores}
Deslocamento já aplicado pelas perguntas comparativas: {comparative_adjustments}

Sua tarefa é APENAS refinar essas intensidades com base no que o gestor escreveu
nas respostas dissertativas — as perguntas fechadas já foram contabilizadas, não
as recalcule. Para cada dimensão, informe um ajuste entre -1.0 e +1.0:
  - positivo se o texto revela prioridade maior do que as respostas fechadas indicam
  - negativo se revela prioridade menor
  - 0 se o texto não traz informação adicional sobre aquela dimensão

Depois, escreva uma justificativa curta (2 a 4 frases) citando o que o gestor disse.

Retorne APENAS um JSON válido no formato:
{{"criteria_adjustments":{{"sustainability":0.0,"performance":0.5,"security":-0.25}},"notes":"texto explicativo"}}
"""
)


# ===============================
# Função principal para extrair pesos e notas
# ===============================
async def llm_extract_weights_and_notes(
    qa_pairs: List[Dict[str, str]],
    numeric_scores: dict,
    comparative_adjustments: dict,
):
    """
    Pede ao LLM um ajuste fino das intensidades (não os pesos finais) a partir das
    respostas dissertativas, mais a justificativa textual. Os pesos em si saem do
    AHP, para que o cálculo continue determinístico e auditável.
    """
    llm = get_llm()
    chain = WEIGHTS_PROMPT | llm

    formatted_qa = "\n".join(f"- {p['pergunta']}\n  → {p['resposta']}" for p in qa_pairs)

    resp_message = await chain.ainvoke(
        {
            "qa_pairs": formatted_qa,
            "numeric_scores": numeric_scores,
            "comparative_adjustments": comparative_adjustments,
        }
    )
    resp_content = resp_message.content

    try:
        j = json.loads(resp_content)
    except Exception:
        # Tenta extrair o JSON do texto se a análise direta falhar
        m = re.search(r"\{.*\}", resp_content, re.S)
        try:
            j = json.loads(m.group(0)) if m else {}
        except Exception:
            j = {}

    # Sanitiza: só critérios conhecidos, só números, sempre dentro de [-1, 1].
    raw = j.get("criteria_adjustments") or {}
    adjustments = {}
    for criterion in ("sustainability", "performance", "security"):
        try:
            value = float(raw.get(criterion, 0.0))
        except (TypeError, ValueError):
            value = 0.0
        adjustments[criterion] = max(-1.0, min(1.0, value))

    return {
        "criteria_adjustments": adjustments,
        "notes": j.get("notes") or (resp_content if not j else ""),
    }


# ===============================
# Embeddings (compartilhado entre RAG e ingestão)
# ===============================
@lru_cache(maxsize=1)
def get_embedding_function():
    """
    Retorna a função de embeddings conforme LLM_PROVIDER (mesma usada no RAG).
    Em cache: HuggingFaceEmbeddings carrega o modelo sentence-transformers do disco,
    o que levava segundos a cada chamada quando era recriado por consulta.
    """
    if LLM_PROVIDER == "openai":
        return OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
    return HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")


# ===============================
# Funções auxiliares RAG (FAISS)
# ===============================
def _faiss_index_ready(path: str) -> bool:
    p = Path(path)
    return (p / "index.faiss").is_file() and (p / "index.pkl").is_file()


# Cache do índice carregado, invalidado pelo mtime do arquivo (a ingestão regrava
# o índice). Sem isso, cada consulta relia ~7MB do disco e reconstruía o objeto.
_index_cache: Dict[str, Any] = {"mtime": None, "index": None}


def _load_faiss_index(embeddings, use_cache: bool = True) -> Optional[FAISS]:
    """Carrega o índice FAISS persistido em VECTOR_DB_PATH, ou None se ainda não existir."""
    if not _faiss_index_ready(VECTOR_DB_PATH):
        return None

    mtime = os.path.getmtime(os.path.join(VECTOR_DB_PATH, "index.faiss"))
    if use_cache and _index_cache["index"] is not None and _index_cache["mtime"] == mtime:
        return _index_cache["index"]

    # allow_dangerous_deserialization: o índice é gerado e lido apenas por nós mesmos
    # (ingestão local), não é um arquivo de origem externa/não confiável.
    index = FAISS.load_local(VECTOR_DB_PATH, embeddings, allow_dangerous_deserialization=True)
    if use_cache:
        _index_cache["mtime"] = mtime
        _index_cache["index"] = index
    return index


def _invalidate_index_cache() -> None:
    _index_cache["mtime"] = None
    _index_cache["index"] = None


def detect_provider_id(file_name: str) -> Optional[str]:
    """
    Descobre a qual provedor um documento pertence pelo nome do arquivo, usando os
    doc_keywords de providers_data. Retorna None quando nenhum termo casa — nesse
    caso o documento é indexado, mas não é atribuído a nenhum provedor (e portanto
    não vira evidência de ninguém), o que é preferível a atribuí-lo ao provedor errado.
    """
    from providers_data import PROVIDERS  # import local evita ciclo de importação

    name = (file_name or "").lower()
    for p in PROVIDERS:
        for kw in p.get("doc_keywords", []):
            if kw in name:
                return p["id"]
    return None


def _format_hit(doc, score: float) -> Dict[str, Any]:
    """Normaliza um resultado do FAISS para o formato consumido pelo frontend."""
    md = doc.metadata or {}
    page_index = md.get("page")
    return {
        "page_content": doc.page_content[:800],
        "score": float(score),
        "file_name": md.get("file_name"),
        # page do PyPDFLoader é 0-indexed; page_label é o rótulo impresso no PDF
        "page": (page_index + 1) if isinstance(page_index, int) else None,
        "page_label": md.get("page_label"),
        "total_pages": md.get("total_pages"),
        "scope": md.get("source"),
        "session_id": md.get("session_id"),
        "provider": md.get("provider"),
    }


def count_chunks_by_provider() -> Dict[str, int]:
    """
    Quantos trechos indexados existem por provedor. Usado para excluir do ranking
    provedores sem nenhuma base documental.
    """
    index = _load_faiss_index(get_embedding_function())
    if index is None:
        return {}

    counts: Dict[str, int] = {}
    for doc in index.docstore._dict.values():
        provider = (doc.metadata or {}).get("provider")
        if provider:
            counts[provider] = counts.get(provider, 0) + 1
    return counts


def rag_query_documents(
    query: str,
    top_k: int = 3,
    session_id: Optional[str] = None,
    provider_id: Optional[str] = None,
):
    """
    Busca por similaridade no índice FAISS.
    - Sempre consulta documentos globais (source=global, de /data/pdf).
    - Se session_id for informado, também consulta documentos da sessão (source=session, session_id=xxx).
    - Se provider_id for informado, restringe aos documentos daquele provedor.

    O filtro por provedor é essencial para as evidências: a similaridade vetorial é
    dominada pelos termos temáticos (energia, data center, segurança), então uma
    busca por "Oracle Cloud: eficiência energética" retorna alegremente trechos da
    AWS se não houver documento da Oracle indexado. Sem o filtro, o relatório
    citaria o documento de um provedor como evidência de outro.
    """
    index = _load_faiss_index(get_embedding_function())
    if index is None:
        return []

    all_results: List[Dict[str, Any]] = []
    # fetch_k alto: o filtro é aplicado após a busca dos vizinhos mais próximos,
    # então provedores com poucos documentos precisam de um pool maior de candidatos.
    fetch_k = 500

    # 1) Documentos globais (sempre)
    try:
        global_filter: Dict[str, Any] = {"source": METADATA_SOURCE_GLOBAL}
        if provider_id:
            global_filter["provider"] = provider_id
        docs_scores = index.similarity_search_with_score(
            query, k=top_k, filter=global_filter, fetch_k=fetch_k
        )
        all_results.extend(_format_hit(d, s) for d, s in docs_scores)
    except Exception:
        pass

    # 2) Documentos da sessão (apenas se session_id ativo)
    if session_id:
        try:
            session_filter: Dict[str, Any] = {
                "source": METADATA_SOURCE_SESSION,
                "session_id": session_id,
            }
            if provider_id:
                session_filter["provider"] = provider_id
            docs_scores = index.similarity_search_with_score(
                query, k=top_k, filter=session_filter, fetch_k=fetch_k
            )
            all_results.extend(_format_hit(d, s) for d, s in docs_scores)
        except Exception:
            pass

    # Ordenar por score (menor distância = mais similar) e pegar top_k
    all_results.sort(key=lambda x: x["score"])
    return all_results[:top_k]


# ===============================
# Ingestão no banco vetorial (com metadados para filtrar global vs sessão)
# ===============================
def _load_and_chunk_paths(file_paths: List[str], doc_metadata: Dict[str, Any]):
    """Carrega arquivos, divide em chunks e anexa doc_metadata a cada chunk."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    docs_total: List = []
    processed = []
    errors = []

    for path in file_paths:
        if not os.path.isfile(path):
            errors.append(f"Arquivo não encontrado: {path}")
            continue
        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".pdf":
                loader = PyPDFLoader(path)
                docs = loader.load()
            elif ext in (".txt", ".text"):
                loader = TextLoader(path, encoding="utf-8")
                docs = loader.load()
            else:
                errors.append(f"Formato não suportado: {path}")
                continue
            chunks = splitter.split_documents(docs)
            file_name = os.path.basename(path)
            provider_id = detect_provider_id(file_name)
            for ch in chunks:
                # doc_metadata traz source="global"/"session", que colide com o
                # metadado "source" do loader (caminho do arquivo). Preservamos o
                # nome do arquivo antes do update para conseguir citar a origem
                # do trecho (arquivo + página) nas evidências.
                ch.metadata["file_name"] = file_name
                if provider_id:
                    ch.metadata["provider"] = provider_id
                ch.metadata.update(doc_metadata)
            docs_total.extend(chunks)
            processed.append((path, len(chunks), provider_id))
        except Exception as e:
            errors.append(f"{path}: {str(e)}")

    return docs_total, processed, errors


def ingest_documents_from_paths(
    file_paths: List[str],
    doc_metadata: Dict[str, Any],
) -> dict:
    """
    Carrega documentos (PDF ou TXT), divide em chunks, anexa doc_metadata a cada chunk,
    gera embeddings e armazena no índice FAISS. Usado tanto para ingestão global (data/pdf)
    quanto por sessão (data/upload).
    """
    docs_total, processed, errors = _load_and_chunk_paths(file_paths, doc_metadata)

    if not docs_total:
        return {
            "chunks": 0,
            "files_processed": 0,
            "files_failed": len(errors),
            "details": [{"file": p, "chunks": n, "provider": prov} for p, n, prov in processed],
            "unassigned_files": [os.path.basename(p) for p, _, prov in processed if not prov],
            "errors": errors,
        }

    embeddings = get_embedding_function()
    # use_cache=False: mutamos o índice aqui (add_documents), então trabalhamos
    # sobre uma instância própria em vez do objeto compartilhado com as consultas.
    index = _load_faiss_index(embeddings, use_cache=False)
    if index is None:
        index = FAISS.from_documents(docs_total, embeddings)
    else:
        index.add_documents(docs_total)

    Path(VECTOR_DB_PATH).mkdir(parents=True, exist_ok=True)
    index.save_local(VECTOR_DB_PATH)
    _invalidate_index_cache()

    return {
        "chunks": len(docs_total),
        "files_processed": len(processed),
        "files_failed": len(errors),
        "details": [{"file": p, "chunks": n, "provider": prov} for p, n, prov in processed],
        # Arquivos sem provedor identificável no nome: são indexados, mas não
        # aparecem como evidência de nenhum provedor. Sinalizado para o admin corrigir.
        "unassigned_files": [os.path.basename(p) for p, _, prov in processed if not prov],
        "errors": errors,
    }
