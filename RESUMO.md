# Resumo da Aplicação — Assistente de Seleção de Provedores de Cloud Computing

**Documento para incorporação em dissertação acadêmica**  
*Descrição do funcionamento e das tecnologias do produto tecnológico*

---

## 1. Descrição geral do produto

O **Assistente de Seleção de Provedores de Cloud Computing** é um sistema web que apoia gestores na escolha de provedores de nuvem (AWS, Microsoft Azure, Google Cloud, Oracle Cloud e IBM Cloud) com base em critérios de **sustentabilidade**, **desempenho** e **segurança**. O produto combina um modelo multicritério (método AHP — *Analytic Hierarchy Process*) com processamento de linguagem natural via modelos de linguagem (LLM) e recuperação de informação (RAG) para produzir um ranking de provedores, pesos dos critérios, justificativa textual e evidências extraídas de documentos.

---

## 2. Objetivo e funcionamento

### 2.1 Objetivo

O sistema tem como objetivo **recomendar o provedor de nuvem mais alinhado às necessidades e preferências da organização**, de forma transparente e reproduzível, a partir de:

- Respostas a um questionário estruturado (perguntas fechadas e abertas).
- Cálculo de pesos dos critérios com apoio de IA (LLM).
- Aplicação do método AHP para agregação dos critérios e geração do ranking.
- Recuperação de evidências em base documental (RAG) para fundamentar a recomendação.

### 2.2 Fluxo de funcionamento

1. **Entrada:** O gestor responde a um questionário em cinco blocos:
   - **Bloco A — Sustentabilidade:** relevância da eficiência energética, uso de energia renovável e visão sobre sustentabilidade na contratação.
   - **Bloco B — Desempenho:** relevância da disponibilidade (uptime), do suporte técnico e exemplos de problemas de desempenho.
   - **Bloco C — Segurança:** relevância de certificações (ex.: ISO 27001, SOC 2, GDPR) e de backup/recuperação de desastres, além de medidas de segurança consideradas indispensáveis.
   - **Bloco D — Comparações indiretas:** priorização entre sustentabilidade, desempenho e segurança em cenários de priorização, renúncia e atenção dos gestores.
   - **Bloco E — Avaliação global:** provedor mais alinhado (AWS, Azure, Google Cloud ou nenhum/on-premise), fator que mais influenciaria mudança de provedor e definição de “provedor ideal”.

2. **Processamento no backend:**
   - As respostas fechadas são convertidas em **escores numéricos** (escala 1–5) por dimensão (sustentabilidade, desempenho, segurança).
   - As **respostas abertas** e os escores são enviados a um **modelo de linguagem (LLM)**, que retorna **pesos normalizados** para os três critérios e uma **justificativa** em texto.
   - Os pesos e uma **matriz de desempenho** dos provedores (scores por critério, pré-definidos e normalizados) alimentam o **método AHP**, que calcula o **score global** de cada provedor e gera o **ranking**.
   - Um módulo **RAG** (*Retrieval-Augmented Generation*) consulta um **banco vetorial** (Chroma) com documentos sobre os provedores e retorna **trechos (evidências)** por provedor para apoio à decisão. A base documental é alimentada por um processo de **ingestão** em duas fontes (ver seção 2.3).

3. **Saída:** A interface exibe:
   - **Ranking final** dos provedores com explicação do método AHP.
   - **Importância relativa dos critérios** (e menção a subcritérios quando aplicável), com barras de peso e texto explicativo.
   - **Dashboard comparativo:** tabela de indicadores normalizados por provedor e por critério, gráficos de distribuição dos pesos e do ranking final.
   - **Justificativa** gerada pelo LLM e **evidências** recuperadas pelo RAG.

### 2.3 Ingestão de documentos e RAG

A base de documentos usada pelo RAG é alimentada por **duas fontes**, com papéis distintos:

1. **Documentos do administrador (globais)**  
   - **Armazenamento:** diretório **data/pdf** no servidor, preenchido pelo administrador do sistema.  
   - **Ingestão:** realizada no **painel de controle** (acesso restrito por senha, via URL `?page=control`). Os arquivos (PDF ou TXT) são lidos, divididos em trechos (*chunks*), convertidos em vetores (embeddings) e indexados no Chroma com metadado **source=global**.  
   - **Uso no RAG:** esses documentos são **sempre** consultados em toda busca por evidências, independentemente do usuário ou da sessão.

2. **Documentos do usuário (por sessão)**  
   - **Armazenamento:** diretório **data/upload/<session_id>**, onde cada usuário/sessão possui sua própria pasta. O envio é opcional e destinado a “dados extras” (ex.: documentos próprios do provedor ou da infraestrutura local).  
   - **Ingestão:** na página **“Anexar documentos extras”** (ingestão), o usuário seleciona arquivos e aciona um único fluxo que (a) envia os arquivos para o servidor e (b) executa a indexação no Chroma com metadado **source=session** e **session_id** da sessão ativa.  
   - **Uso no RAG:** esses documentos são consultados **somente** quando há uma sessão ativa e o identificador de sessão enviado no questionário coincide com o da ingestão; assim, as evidências passam a combinar a base global com os documentos extras daquele usuário.

O banco vetorial (Chroma) mantém uma única coleção; o filtro por **source** e **session_id** nas consultas garante a separação entre base global e base por sessão. Tecnologias envolvidas na ingestão: *loaders* de PDF/TXT (LangChain), *RecursiveCharacterTextSplitter* para segmentação, mesma função de embeddings usada nas consultas (OpenAI ou Hugging Face) e Chroma para persistência e busca por similaridade.

---

## 3. Arquitetura do sistema

A aplicação segue uma **arquitetura cliente–servidor** em duas camadas:

- **Frontend (cliente):** aplicação web organizada em páginas (questionário, resultados, ingestão de documentos do usuário e painel de controle do administrador). Responsável pela coleta do questionário, envio dos dados ao backend, upload e acionamento da ingestão de documentos e exibição dos resultados (ranking, pesos, dashboard, justificativa e evidências).
- **Backend (servidor):** API REST que orquestra o cálculo de escores, a chamada ao LLM, o AHP e o RAG; expõe ainda endpoints de **upload** de arquivos (por sessão), **ingestão global** (data/pdf) e **ingestão por sessão** (data/upload), devolvendo ranking, pesos, matriz de scores por provedor, justificativa e evidências.

A comunicação entre frontend e backend é feita via **HTTP/JSON**. A implantação é realizada em **containers Docker** (um para o frontend e um para o backend), coordenados por *docker-compose*.

---

## 4. Tecnologias utilizadas

### 4.1 Linguagem de programação

- **Python 3.11** — utilizada em todo o projeto (frontend e backend).

### 4.2 Frontend (interface do usuário)

- **Streamlit** — framework em Python para construção de aplicações web interativas. Permite criar formulários (select, text area), botões, tabelas e gráficos sem escrever HTML/JavaScript diretamente. O frontend foi estilizado com **CSS customizado** (incluído via *unsafe_allow_html*) para layout, cores e tipografia.
- **Pandas** — manipulação de dados para montagem de tabelas e dados dos gráficos exibidos no dashboard.
- **Requests** — biblioteca para envio de requisições HTTP (POST) ao backend.

### 4.3 Backend (API e lógica de negócio)

- **FastAPI** — framework web assíncrono para exposição da API REST. Oferece validação de dados (Pydantic), documentação automática (OpenAPI/Swagger) e execução assíncrona.
- **Uvicorn** — servidor ASGI usado para rodar a aplicação FastAPI em produção/desenvolvimento.
- **Pydantic** — definição e validação dos modelos de entrada (respostas do questionário) e de saída (ranking, pesos, evidências, etc.).

### 4.4 Modelo multicritério (AHP)

- **NumPy** e **Pandas** — normalização dos pesos e cálculo do score global por provedor (combinação linear dos scores por critério com os pesos definidos pelo LLM). O ranking é obtido pela ordenação decrescente do score final.

### 4.5 Modelos de linguagem (LLM) e RAG

- **LangChain** — orquestração de chamadas ao LLM (prompts, encadeamento e parsing da resposta). O sistema suporta **OpenAI** (ex.: GPT-4o-mini) ou **Groq** (ex.: LLaMA), configuráveis por variáveis de ambiente.
- **LangChain-Community** — integração com embeddings (OpenAI ou Hugging Face) e com o banco vetorial Chroma.
- **Chroma** — banco de dados vetorial persistente para armazenar representações vetoriais de documentos; utilizado no RAG para busca por similaridade e recuperação de evidências por provedor. Os documentos são indexados com metadados (**source**: global ou session; **session_id** quando for sessão) para que as consultas filtrem a base global e, quando aplicável, a base da sessão ativa.
- **Sentence-Transformers** (via Hugging Face) — modelo de embeddings (*all-MiniLM-L6-v2*) usado quando o provedor LLM configurado é Groq (ou quando não se usa OpenAI), para gerar os vetores dos documentos na ingestão e nas consultas no RAG.
- **PyPDF** — leitura de arquivos PDF durante a ingestão (carregadores LangChain para PDF e texto).

### 4.6 Implantação e ambiente

- **Docker** — containerização do frontend (Streamlit) e do backend (FastAPI + Uvicorn).
- **Docker Compose** — orquestração dos dois serviços, redes, volumes (ex.: persistência do Chroma) e dependência de saúde do backend para subida do frontend.
- **Variáveis de ambiente** — configuração de chaves de API (OpenAI, Groq), modelo do LLM, caminho do Chroma e nome da coleção; no frontend, **ADMIN_PASSWORD** para acesso ao painel de ingestão do administrador (data/pdf), sem hardcode no código.

---

## 5. Componentes principais (resumo)

| Componente        | Função principal                                                                 | Tecnologia / biblioteca principal   |
|-------------------|------------------------------------------------------------------------------------|------------------------------------|
| Interface web     | Exibir questionário, enviar respostas e mostrar ranking, pesos, dashboard e textos | Python, Streamlit, Pandas, CSS     |
| API REST          | Receber questionário, orquestrar pipeline e devolver resultados                    | Python, FastAPI, Uvicorn, Pydantic |
| Escores e pesos   | Converter respostas em escores 1–5 e obter pesos dos critérios                   | Pydantic (regras), LLM (pesos)     |
| Método AHP        | Calcular score global e ranking dos provedores                                    | Python, NumPy, Pandas              |
| LLM               | Gerar pesos normalizados e justificativa a partir do questionário                  | LangChain, OpenAI ou Groq          |
| RAG               | Buscar evidências em documentos por provedor (base global + base da sessão quando ativa) | LangChain, Chroma, embeddings      |
| Ingestão global   | Indexar documentos de data/pdf (administrador); consultados em todas as buscas   | LangChain (loaders, splitter), Chroma |
| Ingestão por sessão | Receber upload do usuário, indexar em data/upload/<session_id>; consultados só na sessão ativa | FastAPI (upload), LangChain, Chroma |
| Implantação       | Executar frontend e backend em ambiente reproduzível                            | Docker, Docker Compose             |

---

## 6. Considerações para a dissertação

Este resumo descreve o **produto tecnológico** desenvolvido: um assistente de seleção de provedores de cloud que integra **questionário estruturado**, **método AHP**, **modelos de linguagem (LLM)** e **RAG** em uma aplicação web implantada em containers. A linguagem e os frameworks (Python, Streamlit, FastAPI, LangChain, Chroma) foram escolhidos para permitir prototipagem ágil, integração com APIs de LLM e reprodutibilidade do ambiente. O documento pode ser incorporado na dissertação na seção destinada à descrição do sistema, funcionamento e stack tecnológica do produto.
