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

Cada trecho indexado carrega identidade própria e proveniência (`chunk_id`,
`document_id`, `content_hash`, `source_name`, `page`, `year`, `scope`,
`ingested_at`). Os identificadores são **determinísticos**, derivados do
conteúdo: reingerir o mesmo arquivo devolve os mesmos ids, e uma evidência
gravada continua apontando para o mesmo trecho depois de o índice ser
reconstruído. Campo sem informação disponível fica nulo, nunca preenchido por
suposição.

## Organização dos módulos do backend

A Fase 0 da diretriz reorganizou o backend em pacotes por responsabilidade
(diretriz §38). O `llm_utils.py`, que concentrava LLM, embeddings, RAG e
ingestão, deixou de existir.

| Pacote | Responsabilidade | Diretriz |
| --- | --- | --- |
| `config/` | única leitura de `os.environ`; limites, versões e escolha de provedor | §23.2, §28.3, §35 |
| `domain/` | motor determinístico: indicadores, pesos, normalização, pontuação | §5–§13 |
| `llm/` | adaptador multi-provedor, prompts versionados, saída validada por schema | §25, §28.2, §35 |
| `guardrails/` | arquivos, credenciais, injeção, limites, encapsulamento, eventos | §22–§25 |
| `rag/` | índice FAISS, ingestão com metadados completos, recuperação | §13–§16 |
| `audit/` | versionamento do questionário, do algoritmo e dos prompts | §27–§28 |
| `preferences.py` | costura questionário → guardrails → prompt → saída validada | — |

**Nenhum módulo fora de `llm/providers.py` pergunta qual é o provedor.** A camada
de domínio recebe um `LLMClient` pronto — condição para que trocar de modelo não
altere regra nenhuma (§28) e para que o Ollama local seja caminho de primeira
classe (§35.2).

### Guardrails (multicamada, sem biblioteca externa)

A §22.1 é explícita: guardrail não é uma biblioteca, e a aplicação não deve
depender de uma para garantir as regras metodológicas centrais.

| Camada | O que faz |
| --- | --- |
| `guardrails/files.py` | extensão, **assinatura real do conteúdo**, tamanho, nome saneado, caminho controlado |
| `guardrails/secrets.py` | credenciais em texto e documento; ação `MASK`/`REJECT`/`WARN` por `.env` |
| `guardrails/injection.py` | heurísticas de prompt injection — registram, não bloqueiam |
| `guardrails/text.py` | limite de tamanho e encapsulamento em `<USER_CONTEXT>` / `<DOCUMENT_CONTEXT>` |
| `llm/client.py` | JSON → Pydantic → aceitar ou rejeitar, com um retry controlado |

Duas decisões que explicam o desenho:

- **A validação de arquivo olha o conteúdo, não a extensão.** Antes bastava
  terminar em `.pdf`; um executável renomeado passava.
- **A injeção é contida pelo encapsulamento, não pela detecção.** As heurísticas
  só registram (`WARN`), porque bloquear por palavra-chave recusaria respostas
  legítimas sem tornar o sistema mais seguro. O que protege é a marcação — e ela
  só vale porque o conteúdo não consegue fechá-la de dentro.

### Camada de indicadores (§5.1, §7 e §39)

Há um indicador para cada pergunta fechada dos blocos A, B e C — mapeamento 1:1,
declarado em `backend/methodology/indicators.json` e ligado ao questionário pelo
`question_id`. As escalas (coeficientes de relevância, rubricas, método do AHP,
desempate) ficam em `backend/methodology/scales.json`. Os dois são **volumes
editáveis** e entram no hash de versão de cada avaliação.

```text
Resposta de relevância (A/B/C)          Comparação par a par (D)
        ↓                                        ↓
coeficiente de relevância                  matriz de Saaty
        ↓                                        ↓
l_j = v_j / Σ v_k   (na dimensão)      W_d  (peso da dimensão)
        └──────────────┬─────────────────────────┘
                       ↓
              w_j = W_d × l_j      (peso global do indicador)
```

Os três níveis são persistidos separadamente em `indicator_weights` (§7: nunca
sobrescrever um nível com outro). Guardar só o peso global tornaria impossível
responder *por que* ele é o que é.

**Escala de relevância (TODO ACADÊMICO 01, decidido: `irrelevante` = 1).**

| Resposta | Coeficiente |
| --- | --- |
| Decisivo | 5 |
| Muito relevante | 4 |
| Relevante | 3 |
| Pouco relevante | 2 |
| Irrelevante | 1 |
| (sem resposta / "não sei") | `null` |

**A regra que mais importa aqui é sobre a ausência.** Indicador sem coeficiente
válido — pergunta não respondida ou "não sei" — fica com peso `None`, **não** `0`.
Zero afirmaria "este indicador não importa", que é precisamente o que não se sabe.
Com a escala em vigor, `1` é uma resposta ("importa pouco") e `null` é a ausência
dela; o código não colapsa as duas.

Dimensão sem nenhum coeficiente válido não cai em pesos iguais: entra em
`dimensions_needing_review` e vira limitação declarada. O fallback silencioso é o
erro mais fácil de cometer aqui porque não parece erro — produz um resultado de
aparência normal sobre um julgamento que o gestor nunca deu.

### Método do AHP

A §6.3 descreve o procedimento da dissertação: somar cada coluna, dividir cada
elemento pela soma da sua coluna, tirar a média aritmética de cada linha. É o
padrão (`column_mean`) e é o que reproduz a fixture obrigatória da §6.5.

O método das potências (`eigenvector`), usado até a Fase 1, continua disponível
por configuração. **Os dois não são intercambiáveis**: para a matriz de
referência da §6.5 eles divergem em ~0,007 no primeiro peso — pequeno, mas acima
de erro de ponto flutuante. Por isso o método escolhido é gravado em cada
avaliação. A matriz normalizada também é persistida (§32.2): é o passo que
permite refazer a conta dos pesos à mão.

### O que ainda não tem fonte

O motor de desempenho por indicador — normalização benefício/minimização,
conjunto comparável `V`, renormalização e agregação — está implementado e
testado, mas **não tem de onde tirar valores**: a extração de evidência é a Fase
2. Até lá o ranking continua saindo da síntese por dimensão, e a resposta declara
isso em `indicator_weights.performance_source`. Nenhum valor por indicador é
inventado para preencher a lacuna.

### Versionamento (§28)

Cada avaliação grava com que **questionário** (`questions_hash` + versão),
**algoritmo** (`SCORING_ALGORITHM_VERSION`), **prompts** (`prompt_id` +
`prompt_version`) e **modelos** (LLM e embedding) foi produzida. O hash é
calculado sobre a forma canônica do `questions.json`: reindentar o arquivo não o
muda, alterar um enunciado sim. O backend lê o arquivo por um volume somente
leitura (`QUESTIONS_JSON_PATH`), porque ele é editável em tempo de execução.

Se o `questions.json` não puder ser lido, a avaliação **não é recusada**: o hash
volta nulo, o motivo fica registrado e a avaliação é marcada como
`COMPLETED_WITH_LIMITATIONS`. Perder a rastreabilidade da versão é ruim; recusar
a avaliação inteira por causa de um volume não montado seria pior.

### 5. Persistência para auditoria

Cada envio do questionário é gravado em um banco **SQLite** em
`backend/data/audit.db` (o mesmo volume persistente do índice FAISS, então o
banco sobrevive a rebuild de imagem). O caminho é configurável por
`AUDIT_DB_PATH`; o acesso é feito por SQLAlchemy, de modo que migrar para
PostgreSQL depois é trocar a URL de conexão.

| Tabela | Conteúdo | Diretriz (§32.1) |
| --- | --- | --- |
| `submissions` | respondente, pesos, λmax, IC, RC, provedor vencedor, justificativa, versões (questionário, algoritmo, LLM, embedding), estado, e os payloads íntegros de entrada e saída | Evaluation + AhpResult |
| `submission_answers` | uma linha por pergunta, com o **enunciado como estava no envio** | QuestionnaireResponse |
| `ahp_judgments` | as comparações par-a-par do bloco D, com a alternativa escolhida e a razão | PairwiseJudgment |
| `submission_rankings` | posição, score e contribuição por critério de cada provedor | RankingResult |
| `llm_runs` | uma linha por chamada à LLM: prompt + versão, provedor, modelo, status, latência, tokens, hash de entrada e saída | LLMRun |
| `guardrail_events` | regra, etapa, ação, motivo e amostra **já mascarada** | GuardrailEvent |
| `rag_queries` + `retrieved_chunks` | consulta executada e os trechos devolvidos, com score e fonte | — |
| `documents` | documentos ingeridos, com provedor, ano, escopo e modelo de embedding | Document |
| `indicator_weights` | coeficiente de relevância, peso local, peso da dimensão e peso global de cada indicador | IndicatorWeight |

`DocumentChunk` da §32.1 **não** foi criada: o índice FAISS já guarda os chunks e
os identificadores são determinísticos. Uma tabela espelho que ninguém escreve
seria pior que a ausência dela.

**Migração.** `init_db()` roda `create_all` e depois um passo aditivo que
acrescenta colunas novas a tabelas que já existiam — `create_all` cria tabelas,
mas não altera as existentes. Só `ADD COLUMN`: nada é removido nem reescrito, e
os envios anteriores continuam legíveis com os campos novos em nulo. Nulo ali
significa "gravado antes de o campo existir", não "falhou".

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

Os testes ficam em `backend/tests/` e **não tocam rede, LLM nem índice
construído** — o modelo e o RAG são substituídos por duplos. O banco usado é
sempre temporário (`tmp_path`).

```bash
make test
```

Ou diretamente, sem subir o compose:

```bash
docker run --rm -v "$PWD/backend:/app" -w /app ufscar-cloud-selector-backend python -m pytest tests -q
```

| Arquivo | Cobre |
| --- | --- |
| `test_pairwise.py` | escala 3/5/7/9, indiferença = 1, recíprocos, regras de validação, frase legível, leitura do formato antigo |
| `test_questionnaire_pairwise.py` | payload da API → julgamentos, recusa de comparação incompleta/incoerente, separação entre blocos, payload de auditoria |
| `test_ahp_matrix.py` | reciprocidade e diagonal unitária, autovetor (A·w = λmax·w), RC dentro e fora do limite, determinismo, equivalência entre formato novo e antigo |
| `test_guardrails_files.py` | extensão, executável renomeado, MIME divergente, tamanho, nome saneado, symlink para fora, quota |
| `test_guardrails_text.py` | credenciais (detecção, mascaramento, modos), os 4 casos adversariais da §42.5, limite de texto, encapsulamento à prova de fechamento |
| `test_llm_contract.py` | prompt versionado, recorte de JSON, retry único, `LLM_OUTPUT_INVALID`, provedor indisponível, registro de execução |
| `test_versioning.py` | hash canônico do questionário, insensível a formatação e sensível a conteúdo, ausência do arquivo |
| `test_rag_metadata.py` | ids determinísticos, ano lido do nome, página em base 0 vs humana, isolamento de escopo |
| `test_db_migration.py` | esquema antigo → migração aditiva, envios preservados, blocos de auditoria novos |
| `test_ahp_reference.py` | **fixture obrigatória da §6.5** (1/5/7/3 → 0.724/0.193/0.083, λmax 3.066, CI 0.033, CR 0.057), divergência entre métodos, matriz circular |
| `test_domain_weights.py` | coeficientes, "não sei" ≠ 0, somas locais/globais = 1, dimensão sem resposta pede revisão, mudança de escala por config, validação da configuração |
| `test_domain_normalization.py` | benefício e minimização, divisão indefinida, rubrica, conjunto `V` comum, `NOT_FOUND` ≠ 0, renormalização, contribuições, empate |
| `test_recommend_pipeline.py` | integração do endpoint: guardrails no fluxo, pesos de indicador, versões, estado, limitações, gravação da auditoria |

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
