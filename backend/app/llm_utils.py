import os
import json
import re
from dotenv import load_dotenv
from langchain.prompts import PromptTemplate
from langchain_community.embeddings import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_community.document_loaders import PyPDFLoader
from langchain_openai import ChatOpenAI

# Carrega variáveis de ambiente
load_dotenv()

# ===============================
# Configurações e variáveis
# ===============================
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")
VECTOR_DB_PATH = os.getenv("VECTOR_DB_PATH", "./chroma_db")
VECTOR_COLLECTION_NAME = os.getenv("VECTOR_COLLECTION_NAME", "providers_docs")


# ===============================
# Escolha dinâmica do provedor LLM
# ===============================
def get_llm():
    """
    Inicializa dinamicamente o LLM conforme o provedor definido no .env.
    """
    if LLM_PROVIDER == "groq":
        from langchain_groq import ChatGroq
        print(f"🔹 Usando Groq API com modelo: {GROQ_MODEL}")
        llm = ChatGroq(
            groq_api_key=GROQ_API_KEY,
            model_name=GROQ_MODEL,
            temperature=0.2,
            max_tokens=1500
        )
    else:
        print(f"🔹 Usando OpenAI API com modelo: {OPENAI_MODEL}")
        llm = ChatOpenAI(
            openai_api_key=OPENAI_API_KEY,
            model_name=OPENAI_MODEL,
            temperature=0.2,
            max_tokens=1500
        )
    return llm


# ===============================
# Prompt para análise de pesos AHP
# ===============================
WEIGHTS_PROMPT = PromptTemplate(
    input_variables=["numeric_scores", "free_texts"],
    template="""
Você é um assistente especialista em Cloud Computing e decisão multicritério (AHP).
O gestor respondeu um questionário sobre sustentabilidade, desempenho e segurança de provedores de nuvem.

Pontuações médias (escala 1–5): {numeric_scores}
Respostas textuais do gestor: {free_texts}

1. Gere pesos normalizados (soma = 1) para as três dimensões:
   - sustainability
   - performance
   - security
2. Justifique brevemente o motivo dos pesos com base nas respostas textuais.

Retorne APENAS um JSON válido no formato:
{{"criteria_weights":{{"sustainability":0.3,"performance":0.5,"security":0.2}},"notes":"texto explicativo"}}
"""
)


# ===============================
# Função principal para extrair pesos e notas
# ===============================
async def llm_extract_weights_and_notes(numeric_scores: dict, free_texts: dict):
    llm = get_llm()
    chain = WEIGHTS_PROMPT | llm
    resp_message = await chain.ainvoke({{"numeric_scores": numeric_scores, "free_texts": free_texts}})
    resp_content = resp_message.content

    try:
        j = json.loads(resp_content)
    except Exception:
        # Tenta extrair o JSON do texto se a análise direta falhar
        m = re.search(r'\{.*\}', resp_content, re.S)
        j = json.loads(m.group(0)) if m else {{"criteria_weights": {}, "notes": resp_content}}
    return j


# ===============================
# Funções auxiliares RAG
# ===============================
def rag_query_documents(query: str, top_k: int = 3):
    """
    Busca por similaridade no Chroma (vector store persistente)
    """
    embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
    chroma = Chroma(
        persist_directory=VECTOR_DB_PATH,
        collection_name=VECTOR_COLLECTION_NAME,
        embedding_function=embeddings
    )
    docs_and_scores = chroma.similarity_search_with_score(query, k=top_k)
    results = [{"page_content": d.page_content[:800], "score": float(s)} for d, s in docs_and_scores]
    return results
