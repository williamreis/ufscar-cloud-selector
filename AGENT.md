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
| A | 6 | dissertativa | justificativa + refinamento das consultas do RAG |
| B – Desempenho operacional | 7–10 | relevância (1–5) | relevância dos indicadores da dimensão |
| B | 11 | dissertativa | justificativa + refinamento das consultas do RAG |
| C – Segurança da informação | 12–15 | relevância (1–5) | relevância dos indicadores da dimensão |
| C | 16 | dissertativa | justificativa + refinamento das consultas do RAG |
| D – Comparações par-a-par | 17–19 | dimensão prioritária + intensidade | **única fonte dos pesos entre as dimensões** |
| E – Avaliação global | 20–25 | dissertativas | requisitos institucionais → **termos de busca por indicador** (§4.5.1); nunca pesos |

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
2. **Prioridades dos critérios** — vetor de prioridades da matriz, acompanhado de
   λmax, do índice de consistência (IC) e da **razão de consistência
   (RC = IC / IR)**, comparada ao limite de 0,10 de Saaty. Como os julgamentos são
   do próprio gestor, o RC mede a coerência real das respostas dele.

   **Com RC > 0,10 a avaliação não prossegue.** A §4.2.3 é explícita: "Caso o
   valor de CR seja superior a 0,10, o sistema informa ao usuário a existência de
   inconsistência nos julgamentos e solicita a revisão das comparações antes do
   prosseguimento do processo de avaliação". O endpoint devolve **409** com
   `AHP_INCONSISTENT_JUDGMENTS`, e a verificação fica logo depois do cálculo dos
   pesos — antes de qualquer chamada à LLM. Julgamentos que se contradizem não
   devem produzir ranking, e pagar a extração documental sobre eles seria gastar
   caro por um resultado que o próprio método declara não utilizável.

   O 409 carrega o que a tela precisa para *solicitar a revisão* em vez de só
   recusar: RC, limite, as três perguntas do bloco D e o **par cujo julgamento
   mais destoa** (`worst_pair`). O diagnóstico é o clássico de Saaty — numa matriz
   consistente vale `a_ij = w_i/w_j`, e o pior par é o de maior desvio logarítmico
   dessa razão. Ele aponta; não corrige. O questionário mostra o painel de revisão
   com atalho para cada comparação, e as respostas ficam onde estão (o rascunho
   vive em `sessionStorage`).

   Duas consequências para quem lê o histórico: envios com RC acima do limite
   **não são mais gravados** — sem resultado produzido não há avaliação a
   registrar —, então `is_consistent = false` no banco só aparece em registros
   anteriores a esta regra. O relatório mantém o modo "resultado preliminar" para
   reexibir esses envios antigos na área de gestão.
3. **Síntese das alternativas** — soma ponderada (Equação 5 da dissertação):
   `S_i = Σ_{j∈V} w'_j × r_ij`. O desempenho `r_ij` vem da extração documental,
   normalizado por benefício ou minimização; o peso `w'_j` é o peso global do
   indicador renormalizado sobre o conjunto comparável. Cada score fica em [0,1]
   e é a soma das suas próprias contribuições — não há fator de cobertura, nem
   nota de reserva para provedor sem evidência.

O relatório também traz um bloco recolhível com **as respostas do questionário**
que o geraram — as 25 perguntas na ordem, com o que foi marcado e com as
dissertativas em branco assinaladas como não respondidas. Elas não vêm da API
(`/api/recommend` devolve só o resultado): no `/results` saem do estado da
aplicação, e na área de gestão saem da tabela `submission_answers`.

A memória de cálculo completa é devolvida pela API e exibida na tela de
resultados, em dois campos:

- `ahp` — julgamentos com a alternativa escolhida, matriz, autovetor, λmax, IC e
  RC: rastreia cada **peso** até a resposta que o gerou;
- `synthesis` — para cada par (provedor × indicador), o valor publicado no
  documento, a unidade ou categoria, o estado da evidência, a fonte, o valor
  normalizado, o peso efetivo e a contribuição; e, por dimensão, a contribuição
  agregada. Rastreia cada **score final** até a aritmética que o produziu e, dali,
  até o trecho de documento que o sustenta;
- `indicator_weights` — os quatro níveis de peso de cada indicador (coeficiente
  de relevância, local, global e efetivo), com o motivo de exclusão quando o
  indicador ficou fora do conjunto comparável.

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

**O SDK não decide por nós.** Todos os chat models são construídos com
`max_retries=0` e `request_timeout` (`LLM_TIMEOUT_S`). Os SDKs da Groq e da OpenAI,
por padrão, repetem o 429 sozinhos e **dormem dentro da chamada** o tempo que o
provedor pedir: a camada de cima nunca via o limite de taxa, não passava a vez
para o fallback, e uma chamada de 3s virava uma de 80s. Nove dessas numa avaliação
davam onze minutos de requisição — além do `proxy_read_timeout` do nginx e de
qualquer paciência. Quem decide esperar ou trocar é o `LLMClient`.

**Cadeia de provedores.** O padrão é Groq com OpenRouter atrás
(`LLM_FALLBACK_PROVIDERS`). O primário só perde a vez quando **não pode atender
agora** — 429, saldo/cota, indisponibilidade, má configuração do próprio elo. Duas
coisas ficam de fora de propósito: chave recusada, que é erro de instalação e
precisa aparecer; e saída reprovada no schema, que é veredito da §25 — procurar
outro modelo até um deles devolver JSON válido trocaria rejeição registrada por
resposta conveniente. Havendo alternativa, o provedor da vez não espera o limite
passar: passa a vez. Só o último elo usa o orçamento de esperas. Quem respondeu
vai em `llm_runs.provider`; de quem era a vez, em `llm_runs.fallback_from`.

Duas armadilhas conhecidas, ambas já cobradas em teste:

- **Teto de tokens.** `LLM_MAX_TOKENS` cobre a resposta inteira, e nos modelos de
  raciocínio o "pensamento" gasta parte dela antes do JSON começar. Com 1500 a
  extração truncava e *toda* dimensão se perdia — com a mensagem enganosa de
  "resposta não contém JSON válido". O default é 4000, e o cliente distingue
  truncamento de resposta malformada porque a ação do operador é diferente.
- **Modelo `:free` do OpenRouter some sem aviso.** O
  `meta-llama/llama-4-maverick:free` passou a responder 404 pedindo a versão paga.
  Um 404 não aciona fallback (é erro de instalação), então o fallback ficava morto.
  Ao trocar, confira <https://openrouter.ai/models?max_price=0>.
- **O sufixo `:free` tem um teto por dia que o saldo não levanta.** Chamadas
  `:free` não consomem crédito e por isso crédito não as libera: elas caem num
  teto de requisições diárias da conta (50/dia sem compra registrada), que responde
  429 com `limit_source: openrouter_free_tier_daily`. Com o Groq no primeiro elo,
  os dois elos caem no mesmo dia e a avaliação sai inteira em `LLM_UNAVAILABLE`.
  Quem tem saldo tira o `:free` de `OPENROUTER_MODEL`; o default do código o
  mantém porque um valor que ninguém escolheu não pode começar a gastar.

**A camada gratuita do Groq não sustenta a extração.** Uma chamada por (provedor ×
dimensão) custa ~6.000 tokens de prompt, contra um teto de 8.000 por minuto: a
partir da segunda chamada o 429 é certo. É por isso que o fallback existe, e não
por precaução — na prática o Groq atende as primeiras e o OpenRouter atende o
resto. Daí também as duas afinações que tiram o desperdício disso:

- **A recusa por cota é lembrada.** O provedor que responde "tente em 11s" fica em
  compasso de espera por esse prazo, e as chamadas seguintes pulam o elo em vez de
  colher o mesmo não. A janela declarada vale por inteiro até `LLM_COOLDOWN_MAX_S`
  — orçamento distinto de `LLM_RATE_LIMIT_MAX_WAIT_S`, porque pular não custa
  espera; confundir os dois truncava a cota diária do Groq (21min) em 60s. O
  último elo da cadeia nunca é pulado.
- **A sondagem é uma só, desde a primeira leva.** Com `LLM_CONCURRENCY=3` as três
  chamadas saem antes de qualquer recusa voltar, e antes cada uma colhia o mesmo
  não — três 429 idênticos do Groq logo depois de "Application startup complete".
  Agora, enquanto o provedor não tiver respondido neste processo, **uma** chamada
  sonda e as outras aguardam o veredito dela. Aguardar, e não pular direto ao
  fallback, é deliberado: com o provedor de pé todas seguem por ele e a avaliação
  sai de um modelo só, que é o que a §27 precisa afirmar no relatório. Assim que
  ele responde, a concorrência volta a correr solta.
- **A espera de cota sobrevive ao reinício.** Ela é gravada em
  `<dir do audit.db>/llm_cooldown.json`, em instante absoluto — o relógio
  monotônico não atravessa reinício, e um prazo relativo viraria janela nova a
  cada subida. Sem isso, todo restart gastava de novo as chamadas de descoberta
  num provedor que já estava em cota diária. É cache: apagar o arquivo só faz a
  próxima leva sondar outra vez, e erro de leitura ou escrita volta a ser memória.
- **Cota diária não se resolve esperando.** Teto por minuto passa em segundos e o
  último elo da cadeia espera por ele; teto por dia (`per day`, `TPD`,
  `free-models-per-day`) só vira na virada, e nenhuma das esperas configuradas o
  alcança — o cliente falha na hora em vez de somar ~17s de backoff por chamada.
  A janela vem do que o provedor declara: a frase "try again in 21m20s" do Groq,
  ou o `X-RateLimit-Reset` em epoch-ms que o OpenRouter aninha no corpo do 429.
- **`LLM_CONCURRENCY=3`.** Serializar fazia sentido quando o teto de tokens do
  Groq era o gargalo; com o OpenRouter atendendo, só somava latência.

Medido de ponta a ponta numa avaliação completa: 209s antes das duas, 77s depois.

### Guardrails (multicamada, sem biblioteca externa)

A §22.1 é explícita: guardrail não é uma biblioteca, e a aplicação não deve
depender de uma para garantir as regras metodológicas centrais.

| Camada | O que faz |
| --- | --- |
| `guardrails/files.py` | extensão, **assinatura real do conteúdo**, tamanho, nome saneado, caminho controlado |
| `guardrails/secrets.py` | credenciais em texto e documento; ação `MASK`/`REJECT`/`WARN` por `.env` |
| `guardrails/injection.py` | heurísticas de prompt injection — registram, não bloqueiam |
| `guardrails/text.py` | limite de tamanho e encapsulamento em `<USER_CONTEXT>` / `<DOCUMENT_CONTEXT>` |
| `llm/client.py` | JSON → Pydantic → aceitar ou rejeitar, com um retry controlado; 429 é espera com backoff, não indisponibilidade; cadeia de fallback quando o provedor não pode atender |

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

#### Cobertura dos Quadros 22 e 24

Os quadros da dissertação e o conjunto que o produto avalia **não coincidem**, e
a divergência é registrada em vez de silenciada. O Quadro 24 lista
"Monitoramento/auditoria", "Throughput" e "Confiabilidade"; o Quadro 22 lista
ainda Estabilidade, Capacidade, Machine Learning e Blockchain. Nenhum desses
vira indicador, porque **só pergunta fechada do Quadro 25 produz coeficiente de
relevância** — sem pergunta não há peso local, e sem peso o indicador não entra
na comparação.

Na direção oposta, quatro indicadores avaliados não têm linha no Quadro 22:
resíduos eletrônicos, circularidade, suporte técnico e backup/recuperação. Vêm do
questionário e das RSLs.

Cada indicador declara em `coverage` que linhas dos Quadros 22/24 operacionaliza
e o exemplo de evidência da coluna correspondente. O que ficou de fora está em
`not_operationalized`, com o motivo tirado do vocabulário que a §4.4 enumera
(dados internos, divulgação heterogênea, sem verificação documental, agregado em
outro indicador, sem pergunta no questionário).

O carregamento recusa duas incoerências: motivo fora do vocabulário, e linha que
aparece ao mesmo tempo coberta e excluída — se as duas afirmações coexistem, uma
é falsa e não se sabe qual.

```bash
make quadros                                    # Quadro 24 e cobertura, em Markdown
python scripts/generate_quadros.py --check      # falha se algo não está rastreado
```

Os quadros são **gerados da configuração**, não transcritos: `indicators.json` é
a mesma fonte que o cálculo lê, então um indicador que entre ou saia do produto
muda o quadro junto. `--check` serve à integração contínua.

#### Rubricas qualitativas — o Quadro 23

`scales.json` traz as duas rubricas da dissertação. `nivel_atendimento` reproduz
o Quadro 23 inteiro: os nomes dos níveis, a **condição da evidência** de cada um
e o valor.

| Nível | Condição da evidência | Valor |
| --- | --- | --- |
| `nao_identificado` | Não há evidência documental suficiente para confirmar o atendimento | — |
| `baixo` | Evidência limitada, indicando atendimento parcial inicial ao indicador | 0,25 |
| `moderado` | Evidência demonstra atendimento parcial ao indicador | 0,50 |
| `alto` | Evidência demonstra atendimento substancial ao indicador | 0,75 |
| `completo` | Evidência demonstra atendimento integral às condições previstas | 1,00 |

Três consequências que o código faz valer:

- **`nao_identificado` vale `null`, não zero.** O quadro registra "—" e o texto
  logo abaixo é explícito: "não recebe valor igual a zero, uma vez que a ausência
  de evidência não é interpretada como desempenho inferior do provedor. Nessa
  situação, aplica-se o procedimento definido na Seção 4.4.1.3". `value_from_category`
  devolve `NOT_FOUND` — o indicador sai do conjunto comparável, ninguém é punido.

- **`nao_identificado` ≠ categoria desconhecida.** A primeira é resposta correta
  do modelo (`NOT_FOUND`); a segunda é saída fora da allowlist (`INVALID`). As
  duas excluem o indicador e dizem coisas opostas ao gestor.

- **a condição vai para o prompt.** `describe_indicators` entrega à LLM o nível
  *com a sua condição*. Sem isso, "escolha entre baixo, moderado, alto e
  completo" não é regra — é rótulo solto, e a classificação vira opinião do
  modelo. A condição volta no relatório (`category_condition`), como a
  justificativa do nível atribuído.

`binario` cobre o parágrafo final do quadro ("indicadores cuja natureza permita
apenas verificar a existência de uma condição objetiva") e preserva a assimetria
dele: `comprovado` = 1,00, `nao_atendido` = 0,00 **com evidência explícita de não
atendimento**, e `nao_identificado` para o silêncio do documento — que não é
nenhum dos dois. Nenhum indicador a usa hoje; ela existe porque o quadro a prevê.

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

### De onde vem o desempenho dos provedores

`evidence.py` é o elo entre o RAG e o motor determinístico:

    indicador → consulta RAG → trechos → LLM (Quadro 26) → validação →
    PerformanceInput → normalização (§9) → agregação (§12)

A consulta é montada **por indicador**, a partir do campo `search_terms` de
`indicators.json` (o Quadro 27 em forma de dado). Não existe mais consulta por
dimensão: a §4.4 determina que "o processo de recuperação não ocorre de forma
aberta ou desvinculada dos critérios da pesquisa", e uma busca por "segurança da
informação" em geral é exatamente isso. Uma consulta por (provedor × indicador),
`top_k` baixo, filtrada pelo provedor no índice.

Os mesmos termos acompanham o indicador dentro do prompt: a §5.2 os descreve como
orientadores "na construção das consultas **e na recuperação das evidências
documentais**", que são duas etapas, não uma.

**Refinamento pelo Bloco E (§4.5.1).** Os requisitos institucionais que o gestor
descreve em texto livre passam por `PROMPT_QUERY_REFINEMENT_V1`, um prompt
auxiliar que os associa aos indicadores já definidos e devolve termos de busca.
Os termos entram **no fim** da consulta, depois dos da pesquisa, no máximo quatro
por indicador e 60 caracteres cada — refinar é ajustar o foco de uma consulta que
já existe, não trocá-la. A ordem importa porque o vetor da consulta é a média do
que está nela.

O limite da §4.5.1 — "não alteram os pesos das dimensões calculados pelo AHP nem
os pesos locais dos indicadores" — é estrutural, não uma verificação: a saída do
prompt é `{indicator_id, terms}`, e o único consumidor é `rag.query_for_indicator`.
Não há campo de peso nem caminho até o cálculo. Falha do prompt não bloqueia a
avaliação; as buscas seguem com os termos da pesquisa. Os termos acrescentados
ficam gravados à parte, em `rag_queries.refined_terms`, para que o registro diga
o que veio da pesquisa e o que veio do gestor.

A interpretação é uma chamada por (provedor × dimensão), com a lista fechada de
indicadores daquela dimensão, as unidades esperadas dos quantitativos e as
categorias permitidas de cada rubrica — o modelo devolve valor publicado ou
categoria, nunca nota.

**O prompt principal.** `PROMPT_EVIDENCE_EXTRACTION_V1` é a implementação literal
do Quadro 26: cada linha do quadro é uma seção nomeada no *system prompt*, com a
instrução operacional reproduzida ao pé da letra. As regras 12–14 (isolamento do
contexto documental, cobertura da lista, formato JSON) não vêm do quadro e ficam
agrupadas à parte, para que se saiba o que é da dissertação e o que a
implementação acrescentou. `test_evidence_extraction.py` tem um teste
parametrizado por linha do Quadro 26 — editar o prompt e derrubar uma instrução
quebra a suíte.

A saída segue os campos que a §5.4 enumera: indicador analisado, evidência
identificada (`summary`), natureza, **valor ou característica extraída**
(`extracted_value`, mais `value`/`unit` ou `category` na forma que o cálculo
consome) e referência à fonte. `extracted_value` guarda o que o documento diz
("ISO/IEC 27001, ISO/IEC 27017 e SOC 2") ao lado do que o modelo classificou
(`level_4` → 1,00), e é isso que torna a classificação conferível no relatório.

Três validações decidem o que sobrevive (§19):

- **fonte verificável** — a `source_chunk_id` citada precisa estar entre os
  `chunk_id` entregues na chamada. Identificador inventado vira `INVALID`;
- **categoria na allowlist** — categoria fora da rubrica vira `INVALID`, não uma
  nota aproximada. A conversão em número é sempre da rubrica;
- **unidade esperada** — a unidade informada tem de estar entre as
  `expected_units` do indicador. É validação de formato (§5.4), e o caso que a
  motiva é o CUE: o Quadro 22 o define como razão (emissão ÷ energia dos
  equipamentos), e um total absoluto em `tCO2e` passaria pela normalização por
  minimização fazendo o provedor **maior** perder por ser maior;

- **unidade comparável** — se dois provedores publicam o mesmo indicador em
  unidades diferentes ("90 %" e "0,9 ratio"), o indicador inteiro sai da
  comparação, para todas as alternativas.

Falha da LLM, indicador omitido da resposta e ausência de trecho recuperado
levam todos ao mesmo lugar: `NOT_FOUND` com o motivo registrado. Nenhum valor é
inventado para preencher a lacuna, e `indicator_weights.performance_source`
declara a procedência (`evidence_extraction`).

Não há mais notas fixas no código. O antigo dicionário `scores` de
`providers_data.py`, que era a fonte real do ranking antes desta mudança, foi
removido — o arquivo agora só liga documento a provedor pelo nome do arquivo.

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

### 6.1. Base documental do RAG, dentro da área de gestão

O painel **Base documental (RAG)**, no rodapé do `/admin`, executa a ingestão dos
documentos de `data/pdf` sem sair da área de gestão — o `/control` continua
existindo, com o mesmo pipeline e a mesma senha.

| Rota | O que faz |
| --- | --- |
| `GET /api/admin/rag/status` | inventário: arquivos em `data/pdf`, documentos já ingeridos, estado do índice, trechos por provedor, mais o job em curso |
| `POST /api/admin/rag/ingest` | **inicia** a ingestão e responde `202` na hora; com `{"files": [...]}`, só os arquivos indicados |
| `GET /api/admin/rag/ingest` | estado do job (rota leve, própria para polling de poucos em poucos segundos) |

**A ingestão não é síncrona, e não pode ser.** Indexar a base inteira leva
minutos; o nginx do frontend corta em 300s (`proxy_read_timeout` em
`frontend/nginx.conf`), e a resposta síncrona virava **504 na tela enquanto o
backend seguia indexando** — o pior dos dois mundos, porque o administrador via
"falhou" e podia disparar tudo de novo. Agora a rota inicia o trabalho e o painel
acompanha; um `POST` durante uma ingestão em curso responde `409` em vez de
enfileirar uma segunda.

Os arquivos são processados **um por vez**: é o que torna o progresso real (`3 de
12`, com o nome do arquivo) e o que mantém indexado o que já terminou se o
processo cair no meio. O estado do job vive em memória, num processo só —
reiniciar o backend perde o acompanhamento, não o que já foi indexado. Como o
job também vem no `/rag/status`, recarregar a página no meio da ingestão
reencontra o trabalho em curso.

O inventário cruza **três fontes que podem discordar**, e mostra a discordância
em vez de deduzir uma da outra:

- o **diretório** `data/pdf` — o que existe no servidor;
- a tabela `documents` — o que já foi ingerido (chave: o hash do conteúdo, então
  editar o arquivo o devolve para "pendente");
- o **índice FAISS** — o que de fato responde às buscas.

Duas consequências ficam explícitas na tela porque afetam a decisão do
administrador:

- **reingerir duplica vetores.** `rag.ingest_paths` faz `add_documents` sobre o
  índice existente e não remove os chunks da ingestão anterior do mesmo
  documento. O registro no banco é idempotente; o índice não é. É por isso que a
  seleção por arquivo existe — reingerir só o pendente em vez do diretório todo.
- **provedor sem trecho indexado fica fora do ranking** (a cobertura documental
  do `/api/recommend`), então os contadores por provedor incluem os zerados.

A seleção por nome passa por `guardrails.resolve_within`: um nome vindo da
requisição não alcança arquivo fora de `data/pdf`.

Os diretórios (`data/pdf`, `data/upload`), o pipeline compartilhado e o job
saíram do `main.py` para `backend/app/documents.py` — `main` importa o router de
`admin`, e as duas pontas precisam da mesma função de ingestão. O `/control` usa
as mesmas rotas do painel; `POST /api/documents/ingest-global` continua existindo
e continua síncrono, para uso por linha de comando, onde esperar não é problema.

## Desvios assumidos em relação à diretriz

A dissertação é a especificação e não se altera. Onde o código se afasta dela, o
afastamento é decisão registrada do autor — não descuido. Os três abaixo foram
apresentados e mantidos; cada um está anotado também no arquivo onde vive.

| Diretriz | O que o código faz | Onde está registrado |
| --- | --- | --- |
| §5.3 cita "NumPy e Pandas" | Só NumPy. O Pandas saiu com a síntese distributiva; a Equação 5 é aritmética de vetores | `requirements.txt` |
| §4.4.1.3 só prevê excluir **indicadores** | Provedor sem documentos indexados fica fora do conjunto avaliado. A diretriz não define como o conjunto de alternativas é formado; a alternativa literal colapsaria o ranking em zeros | `main.py`, passo 5 |
| Quadro 22 lista 22 indicadores; Quadro 25 traz 13 perguntas | Prevalece o Quadro 25 — correspondência 1:1 com as perguntas fechadas. A §4.4.1.4 faz o peso local depender delas, então indicador sem pergunta não entra na Equação 5 | `indicators.json`, bloco `not_operationalized` |

Além destes, o código faz cinco coisas que a diretriz não descreve nem proíbe: o
diagnóstico do par mais inconsistente (§4.2.3), a regra de unidade divergente
(§4.4.1.1), os estados `PARTIAL` e `INVALID` (§4.4.1), os limites do refinamento
pelo Bloco E (§4.5.1) e o método de ponderação alternativo (§4.2.3). Só o segundo
pode alterar um ranking — ele retira da comparação um indicador que a diretriz
incluiria.

## Documentos do repositório

`RESUMO.md` descreve o produto para incorporação na dissertação. Ele é **coberto
por testes** (`test_resumo.py`): tecnologia que sai do projeto não pode continuar
citada lá, e os números que ele afirma — 25 perguntas, 5 níveis de relevância, 13
indicadores, limiar 0,10, quantidade de prompts — são conferidos contra a
configuração em vigor. A versão anterior descrevia Streamlit, Chroma e uma LLM
que calculava os pesos; nada conferia, e por isso ninguém percebeu.

O documento também carrega uma seção de **divergências conhecidas** com o texto
atual da dissertação. Ela é a única parte do arquivo autorizada a citar
tecnologia removida, e os testes a excluem da busca por termos banidos.

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
| `test_llm_contract.py` | prompt versionado, recorte de JSON, retry único, `LLM_OUTPUT_INVALID`, provedor indisponível, registro de execução, espera e repetição no limite de taxa (429), cadeia Groq → OpenRouter e o que não a aciona |
| `test_versioning.py` | hash canônico do questionário, insensível a formatação e sensível a conteúdo, ausência do arquivo |
| `test_resumo.py` | RESUMO.md descreve o produto que existe: não cita tecnologia removida, as citadas estão declaradas, questionário/prompts/limiares conferem com a configuração |
| `test_quadro_coverage.py` | toda linha dos Quadros 22 e 24 tem destino declarado, conjunto operacional = perguntas fechadas, exclusões com motivo do vocabulário, recusa de registro incoerente, `--check` do gerador |
| `test_rubrics.py` | fidelidade ao Quadro 23 (níveis, condições e valores), `nao_identificado` ≠ 0 e ≠ categoria inválida, assimetria do modo binário, validação da configuração, condição no prompt |
| `test_rag_metadata.py` | ids determinísticos, ano lido do nome, página em base 0 vs humana, isolamento de escopo |
| `test_db_migration.py` | esquema antigo → migração aditiva, envios preservados, blocos de auditoria novos |
| `test_ahp_matrix.py` (porta) + `test_recommend_pipeline.py` | RC > 0,10 devolve 409 antes de qualquer chamada à LLM, aponta as comparações e o pior par, não grava o envio, e a correção recupera a avaliação |
| `test_ahp_reference.py` | **fixture obrigatória da §6.5** (1/5/7/3 → 0.724/0.193/0.083, λmax 3.066, CI 0.033, CR 0.057), divergência entre métodos, matriz circular |
| `test_domain_weights.py` | coeficientes, "não sei" ≠ 0, somas locais/globais = 1, dimensão sem resposta pede revisão, mudança de escala por config, validação da configuração |
| `test_domain_normalization.py` | benefício e minimização, divisão indefinida, rubrica, conjunto `V` comum, `NOT_FOUND` ≠ 0, renormalização, contribuições, empate |
| `test_query_refinement.py` | Bloco E → termos de busca: termos entram na consulta e depois dos da pesquisa, indicador inventado descartado, termo longo cortado, deduplicação, falha não bloqueia, schema sem campo de peso |
| `test_evidence_extraction.py` | consulta por indicador, validação da saída da LLM (fonte inventada, categoria fora da rubrica, quantitativo sem valor), unidades divergentes, omissão e indisponibilidade → `NOT_FOUND`, evidência → ranking ponta a ponta |
| `test_recommend_pipeline.py` | integração do endpoint: guardrails no fluxo, ranking vindo das evidências, RAG por indicador, isolamento do prompt de extração, pesos de indicador, versões, estado, limitações, gravação da auditoria |

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
