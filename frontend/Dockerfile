# ===============================
#  Dockerfile - Frontend (Streamlit)
# ===============================

FROM python:3.11-slim AS base

# Define o diretório de trabalho
WORKDIR /frontend

# Copia dependências do backend para aproveitar a mesma stack
COPY frontend/requirements.txt /frontend/requirements.txt

# Instala dependências do sistema, Streamlit e dependências mínimas
# Adicionado 'build-essential'
RUN apt-get update && apt-get install -y build-essential curl \
    && pip install --no-cache-dir -r /frontend/requirements.txt

# Copia o código do frontend
COPY frontend/main.py /frontend/main.py

# Define porta do Streamlit
EXPOSE 8501

# Desativa o prompt interativo do Streamlit
ENV STREAMLIT_SERVER_HEADLESS=true

# Evita cache de estado
ENV STREAMLIT_SERVER_FILE_WATCHER_TYPE=none

# Comando padrão de execução
CMD ["streamlit", "run", "main.py", "--server.port=8501", "--server.address=0.0.0.0"]
