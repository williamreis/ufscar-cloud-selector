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

O agente recebe as respostas do gestor ao questionário, organizado em cinco blocos:

| Bloco | Perguntas | Tipo | Uso |
| --- | --- | --- | --- |
| A – Sustentabilidade | 1–5 | relevância (1–5) | relevância dos indicadores da dimensão |
| A | 6 | dissertativa | contexto para a justificativa |
| B – Desempenho operacional | 7–10 | relevância (1–5) | relevância dos indicadores da dimensão |
| B | 11 | dissertativa | contexto para a justificativa |
| C – Segurança da informação | 12–15 | relevância (1–5) | relevância dos indicadores da dimensão |
| C | 16 | dissertativa | contexto para a justificativa |
| D – Comparações par-a-par | 17–19 | dimensão prioritária + intensidade | **única fonte dos pesos entre as dimensões** |
| E – Avaliação global | 20–25 | dissertativas | requisitos obrigatórios e perfil desejado por dimensão |

As escalas dos blocos A/B/C e D são deliberadamente separadas: a escala 1–5 mede
a **relevância individual** de um indicador dentro de uma dimensão, enquanto a
escala de Saaty mede a **preferência relativa** entre duas dimensões. Converter
uma na outra produziria julgamentos que o gestor nunca informou, então a média
1–5 por dimensão é reportada como contexto (`ahp.relevance_by_criterion`) e não
entra na matriz.

#### O bloco D em duas etapas

Cada comparação pergunta primeiro **qual dimensão** deve ter maior prioridade —
a da esquerda, a da direita, ou nenhuma ("igual importância") — e só então, se
houver uma preferida, **com que intensidade**. Antes eram nove frases prontas por
par (27 no formulário inteiro), quase todas iguais entre si; a informação AHP é
exatamente a mesma, a leitura é que ficou menor. Marcar igual importância não
abre a pergunta de intensidade: a razão já é 1. Trocar a dimensão preferida
descarta a intensidade anterior, porque mantê-la assumiria uma força que o gestor
não declarou na nova direção.

A resposta trafega e é gravada em forma semântica, **sem número**:

```json
{ "left": "sustainability", "right": "security",
  "preference": "security", "intensity": "strong" }
```

A conversão para a escala de Saaty acontece só no servidor, em
`backend/app/pairwise.py` (moderadamente = 3, fortemente = 5, muito fortemente =
7, extremamente = 9, igual = 1), junto com a direção — preferência pela dimensão
à direita vira o recíproco. Como não existe campo que carregue a razão, não há
peso vindo do cliente para ser adulterado; e uma comparação incompleta
("prefiro segurança", sem intensidade) ou incoerente ("igual" com intensidade) é
recusada com **422** antes de virar peso.

O questionário declara o par no próprio `questions.json`, e o processamento é
genérico pelo tipo — não há lógica presa aos ids `comp_sust_perf`/`comp_sust_sec`/
`comp_perf_sec`:

```json
{ "id": "comp_sust_sec", "type": "pairwise",
  "label": "**18.** Entre Sustentabilidade e Segurança da Informação, …",
  "pair": { "left": "sustainability", "right": "security" },
  "processing": { "purposes": ["ahp_pairwise"], "ahp_input": true, "llm_interpretation": false } }
```

Envios anteriores à mudança guardaram a alternativa em texto e nenhum campo
`pairwise`. Eles continuam sendo lidos (`pairwise.from_legacy_choice`, acionado
pelo mapa `LEGACY_PAIRWISE_QUESTIONS`) e produzem exatamente a mesma matriz — não
houve migração de dados nem alteração de esquema. Para a auditoria continuar
tendo uma coluna com o que foi respondido, `submission_answers.choice` e
`ahp_judgments.choice` recebem a frase legível reconstruída da resposta
estruturada ("Segurança da Informação é fortemente mais importante que
Sustentabilidade"), enquanto o julgamento estruturado permanece íntegro no
`request_json`.

### 2. Justificativa com LLM

Um Modelo de Linguagem de Grande Escala (LLM) recebe todas as perguntas e
respostas (fechadas e dissertativas), a relevância média por dimensão e os pesos
já calculados, e redige **apenas** a justificativa textual da decisão.
O LLM não calcula, ajusta nem propõe pesos.

### 3. Processo de Hierarquia Analítica (AHP)

O Analytic Hierarchy Process (AHP) é um método de tomada de decisão multicritério.
O cálculo é determinístico e auditável, em três etapas:

1. **Matriz de comparação par a par** — montada diretamente das perguntas 17–19:
   a dimensão prioritária e a intensidade viram uma razão de Saaty (ver "O bloco
   D em duas etapas"), e o recíproco preenche o par inverso. Com três dimensões,
   os três julgamentos preenchem a matriz inteira. Pares não respondidos ficam em
   1 (indiferença) e são listados em `ahp.missing_judgments`.
2. **Prioridades dos critérios** — autovetor principal da matriz (método das
   potências), acompanhado de λmax, do índice de consistência (IC) e da
   **razão de consistência (RC = IC / IR)**, comparada ao limite de 0,10 de Saaty.
   Como os julgamentos são do próprio gestor, o RC mede a coerência real das
   respostas dele. Com **RC > 0,10** o relatório para de apresentar um provedor
   como recomendação: o topo do ranking passa a "1º lugar (resultado
   preliminar)", o bloco de consistência explica a contradição e oferece
   **Revisar comparações**, que volta ao bloco D com as respostas preservadas (o
   rascunho do questionário vive em `sessionStorage`).
3. **Síntese das alternativas** — modo distributivo: as notas de referência de
   cada provedor são normalizadas dentro de cada critério e agregadas pelos pesos.
   As prioridades finais somam 1 entre os provedores.

O relatório também traz um bloco recolhível com **as respostas do questionário**
que o geraram — as 25 perguntas na ordem, com o que foi marcado e com as
dissertativas em branco assinaladas como não respondidas. Elas não vêm da API
(`/api/recommend` devolve só o resultado): no `/results` saem do estado da
aplicação, e na área de gestão saem da tabela `submission_answers`.

A memória de cálculo completa é devolvida pela API e exibida na tela de
resultados, em dois campos:

- `ahp` — julgamentos com a alternativa escolhida, matriz, autovetor, λmax, IC e
  RC: rastreia cada **peso** até a resposta que o gerou;
- `synthesis` — para cada par (provedor × critério), a nota bruta, o denominador
  da normalização, a normalizada, o peso e a contribuição, mais a soma que fecha
  em 1: rastreia cada **score final** até a aritmética que o produziu.

### 4. Geração com Recuperação de Contexto (RAG)

Para fornecer evidências que sustentem as recomendações, o agente utiliza um sistema de Geração com Recuperação de Contexto (Retrieval-Augmented Generation – RAG).
O sistema RAG consulta um repositório vetorial de documentos para encontrar informações relevantes sobre o desempenho de cada provedor em relação aos diferentes critérios.

### 5. Persistência para auditoria

Cada envio do questionário é gravado em um banco **SQLite** em
`backend/data/audit.db` (o mesmo volume persistente do índice FAISS, então o
banco sobrevive a rebuild de imagem). O caminho é configurável por
`AUDIT_DB_PATH`; o acesso é feito por SQLAlchemy, de modo que migrar para
PostgreSQL depois é trocar a URL de conexão.

| Tabela | Conteúdo | DIRETRIZ (seção 23) |
| --- | --- | --- |
| `submissions` | respondente, pesos, λmax, IC, RC, provedor vencedor, justificativa, modelo de LLM usado, e os payloads íntegros de entrada e saída | QUESTIONNAIRES + ANALYSIS_SESSIONS + AHP_RESULTS |
| `submission_answers` | uma linha por pergunta, com o **enunciado como estava no envio** | QUESTIONNAIRE_RESPONSES |
| `ahp_judgments` | as comparações par-a-par do bloco D, com a alternativa escolhida e a razão | AHP_COMPARISONS |
| `submission_rankings` | posição, score e contribuição por critério de cada provedor | — |

Duas camadas de fidelidade convivem de propósito: colunas normalizadas (o que o
dashboard agrega sem abrir JSON) e `request_json`/`response_json` (o payload
íntegro, para análises futuras que o modelo de hoje não previu). O `id` do
registro também é o **trace_id** da execução (DIRETRIZ, seção 22) e é exibido no
rodapé do relatório.

Falha de gravação **não** descarta o resultado: o relatório volta com
`submission_id: null` e a tela avisa, em vermelho, que aquele envio não entrou no
registro de auditoria.

### 6. Área de gestão (`/admin`)

Tela autenticada para consultar os envios: indicadores agregados, gráficos
(peso médio das dimensões, provedor em 1º lugar, envios por dia, cargos),
tabela paginada com busca por e-mail ou cargo e exportação em CSV.

Cada envio abre em `/admin/:id`, com duas abas:

- **Relatório** — reexibe o `<Report/>` a partir do `response_json` gravado,
  com as respostas junto. É a mesma tela que o gestor viu, não uma reconstrução:
  o corpo do `/results` foi extraído para `components/Report.tsx` justamente para
  não existirem duas versões da mesma tela para manter em sincronia. O
  `submission_id` é injetado na leitura, porque ele é atribuído depois da
  serialização em `/api/recommend` e portanto não está no JSON gravado.
- **Respostas do questionário** — as linhas normalizadas do banco (comparações
  par-a-par e as 25 respostas com o enunciado da época). É o que sustenta a
  auditoria caso o formato do JSON mude no futuro.

**Exclusão.** `DELETE /api/admin/submissions/{id}` remove o envio e, em cascata,
respostas, julgamentos e ranking — via `cascade="all, delete-orphan"` somado ao
`PRAGMA foreign_keys=ON`. Não há lixeira: o registro sai do banco. A interface
confirma em um diálogo que nomeia o respondente, o cargo, a data e o trace_id
antes de chamar o endpoint.

A autenticação usa **senha única de administrador conferida no servidor**
(`ADMIN_PASSWORD` no `backend/.env`), que devolve um token assinado com
HMAC-SHA256 carregando a própria expiração — não há sessão em memória para se
perder num restart. Detalhes que importam:

- sem `ADMIN_PASSWORD` definido, a área responde **503** e nenhuma senha é aceita
  (*fail closed*);
- trocar a senha invalida todos os tokens já emitidos;
- 5 tentativas erradas por IP colocam o IP em espera por 5 minutos;
- o token vive em `sessionStorage`, some ao fechar a aba, e vale 8h por padrão
  (`ADMIN_TOKEN_TTL`).

O `/control` (ingestão global de documentos) passou a usar essa mesma
autenticação: antes a senha era comparada **no bundle do navegador** e o endpoint
`/api/documents/ingest-global` ficava aberto a qualquer requisição.

## Testes

Os testes cobrem o caminho determinístico do bloco D — conversão para Saaty,
recíprocos, validação, propriedades da matriz e compatibilidade com os envios no
formato antigo. Ficam em `backend/tests/` e não tocam banco, rede nem LLM.

```bash
docker run --rm -v "$PWD/backend:/src" -w /src ufscar-cloud-selector-backend \
  sh -c "pip install -q pytest && python -m pytest tests -q"
```

Localmente, com as dependências instaladas (`requirements-dev.txt`):
`pytest backend/tests`.

| Arquivo | Cobre |
| --- | --- |
| `test_pairwise.py` | escala 3/5/7/9, indiferença = 1, recíprocos, regras de validação, frase legível, leitura do formato antigo |
| `test_questionnaire_pairwise.py` | payload da API → julgamentos, recusa de comparação incompleta/incoerente, separação entre blocos, payload de auditoria |
| `test_ahp_matrix.py` | reciprocidade e diagonal unitária, autovetor (A·w = λmax·w), RC dentro e fora do limite, determinismo, equivalência entre formato novo e antigo |

## API Endpoint

O backend expõe o seguinte endpoint de API:

*   **POST /api/recommend**

Este endpoint recebe as respostas do questionário e retorna uma lista ranqueada de provedores de nuvem.

    **Request Body:**

    O corpo da requisição deve ser um objeto JSON com a seguinte estrutura:

    ```json
    {
      "respondent": "gestor@ufscar.br",
      "session_id": "b3f1c2d4",
      "answers": [
        {
          "question_id": "sust_q1",
          "question_text": "**1.** Ao selecionar um provedor de Cloud Computing, qual é a relevância da eficiência energética dos data centers?",
          "choice": "Muito relevante",
          "text": null
        },
        {
          "question_id": "comp_perf_sec",
          "question_text": "**19.** Entre Desempenho Operacional e Segurança da Informação…",
          "choice": null,
          "text": null,
          "pairwise": {
            "left": "performance",
            "right": "security",
            "preference": "security",
            "intensity": "moderate"
          }
        },
        {
          "question_id": "req_sec",
          "question_text": "**22.** Existe algum requisito relacionado à Segurança da Informação…",
          "choice": null,
          "text": "Precisamos de conformidade com a LGPD e ISO/IEC 27001."
        }
      ]
    }
    ```

    Cada item de `answers` corresponde a uma pergunta do `questions.json`:
    `choice` para as fechadas, `text` para as dissertativas e `pairwise` para as
    comparações do bloco D. O `question_text` acompanha a resposta para que o LLM
    saiba o que foi perguntado.

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
