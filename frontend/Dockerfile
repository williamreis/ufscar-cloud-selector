# ===============================
#  Dockerfile - Frontend (Streamlit)
# ===============================

FROM python:3.11-slim AS base

# Define o diretório de trabalho
WORKDIR /app

# Copia dependências do backend para aproveitar a mesma stack
COPY ../requirements.txt /app/requirements.txt

# Instala Streamlit e dependências mínimas
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copia o código do frontend
COPY ./main.py /app/main.py

# Define porta do Streamlit
EXPOSE 8501

# Desativa o prompt interativo do Streamlit
ENV STREAMLIT_SERVER_HEADLESS=true

# Evita cache de estado
ENV STREAMLIT_SERVER_FILE_WATCHER_TYPE=none

# Comando padrão de execução
CMD ["streamlit", "run", "main.py", "--server.port=8501", "--server.address=0.0.0.0"]
