# ===============================
#  Dockerfile - Backend (FastAPI)
# ===============================

# Etapa base: Python leve e otimizado
FROM python:3.11-slim AS base

# Define o diretório de trabalho
WORKDIR /app

# Copia arquivos de dependências primeiro (para melhor cache)
COPY requirements.txt /app/requirements.txt

# Instala dependências do sistema e Python
RUN apt-get update && apt-get install -y build-essential wget curl cargo \
    && pip install --no-cache-dir -r /app/requirements.txt

# Copia o restante do código da aplicação
COPY backend/app /app/app
COPY backend/.env /app/.env
COPY backend/scripts /app/scripts

# Cria diretório persistente para Chroma
RUN mkdir -p /app/chroma_db
VOLUME ["/app/chroma_db"]

# Expor a porta padrão do FastAPI
EXPOSE 8000

# Comando padrão (usando uvicorn)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
