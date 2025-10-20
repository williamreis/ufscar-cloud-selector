# Agente de Seleção de Provedores de Nuvem da UFSCar
Este documento descreve o agente responsável pelo processo de seleção de provedores de computação em nuvem na UFSCar.

## Visão Geral do Projeto

O UFSCar Cloud Selector é uma ferramenta desenvolvida para auxiliar gestores na escolha do provedor de nuvem mais adequado às suas necessidades específicas.
O projeto é composto por um frontend, responsável por coletar as informações do gestor por meio de um questionário, e um backend, que processa essas informações e gera uma lista ranqueada de provedores de nuvem.

## Arquitetura do Backend

O backend é uma aplicação em Python desenvolvida com o framework FastAPI.
Ele expõe um único endpoint de API que orquestra todo o processo de recomendação.
Esse processo envolve vários componentes principais:

### 1. Processamento do Questionário

O agente recebe as respostas do gestor ao questionário.
As questões fechadas são convertidas em escores numéricos, enquanto as questões abertas são utilizadas para ajustar os pesos dos critérios de seleção.

### 2. Extração de Pesos com LLM

Um Modelo de Linguagem de Grande Escala (LLM) é utilizado para analisar as respostas textuais do gestor.
O LLM extrai um conjunto de pesos para os diferentes critérios de seleção (por exemplo: sustentabilidade, desempenho, segurança) e gera um resumo das prioridades do gestor.

### 3. Processo de Hierarquia Analítica (AHP)

O Analytic Hierarchy Process (AHP) é um método de tomada de decisão multicritério.
O agente utiliza os pesos extraídos pelo LLM como entrada para o modelo AHP, que então calcula uma pontuação para cada provedor de nuvem com base em um conjunto pré-definido de dados dos provedores.

### 4. Geração com Recuperação de Contexto (RAG)

Para fornecer evidências que sustentem as recomendações, o agente utiliza um sistema de Geração com Recuperação de Contexto (Retrieval-Augmented Generation – RAG).
O sistema RAG consulta um repositório vetorial de documentos para encontrar informações relevantes sobre o desempenho de cada provedor em relação aos diferentes critérios.

## API Endpoint

O backend expõe o seguinte endpoint de API:

*   **POST /api/recommend**

Este endpoint recebe as respostas do questionário e retorna uma lista ranqueada de provedores de nuvem.

    **Request Body:**

    O corpo da requisição deve ser um objeto JSON com a seguinte estrutura:

    ```json
    {
      "closed_questions": {
        "sustainability": 4,
        "performance": 5,
        "security": 3
      },
      "free_texts": {
        "sustainability_text": "A sustentabilidade é importante, mas não uma prioridade principal.",
        "performance_text": "Alto desempenho é essencial para nossas aplicações.",
        "security_text": "Precisamos estar em conformidade com o GDPR."
      }
    }
    ```

    **Response Body:**

    O corpo da resposta é um objeto JSON com a seguinte estrutura:

    ```json
    {
      "ranking": [
        {
          "provider": "aws",
          "score": 0.85
        },
        {
          "provider": "gcp",
          "score": 0.75
        },
        {
          "provider": "azure",
          "score": 0.65
        }
      ],
      "criteria_weights": {
        "sustainability": 0.2,
        "performance": 0.5,
        "security": 0.3
      },
      "notes": "O gestor prioriza desempenho e segurança.",
      "evidences": {
        "aws": [
          "A AWS possui uma infraestrutura global que oferece baixa latência.",
          "A AWS conta com um conjunto abrangente de certificações de segurança."
        ],
        "gcp": [
          "O Google Cloud é reconhecido por sua rede de alto desempenho.",
          "O Google Cloud possui um forte compromisso com a sustentabilidade."
        ],
        "azure": [
          "O Microsoft Azure oferece uma ampla gama de serviços para empresas.",
          "O Azure possui forte presença na Europa."
        ]
      }
    }
    ```
