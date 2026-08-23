# Resumo da Aplicação — Plataforma de Seleção de Provedores de Cloud Computing

**Documento para incorporação em dissertação acadêmica**
*Descrição do funcionamento e das tecnologias do produto tecnológico*

> Este documento descreve a versão em vigor do produto e é mantido em sincronia
> com o código. As seções abaixo seguem a numeração do Capítulo 5 da dissertação
> para facilitar a conferência. Alterações no produto que não apareçam aqui são
> divergência, não versão nova.

---

## 1. Descrição geral do produto

A **Plataforma de Seleção de Provedores de Cloud Computing** é uma aplicação web
que apoia gestores de Tecnologia da Informação na análise comparativa de
provedores de nuvem, considerando as dimensões de **sustentabilidade**,
**segurança da informação** e **desempenho operacional**.

O produto operacionaliza o modelo multicritério da pesquisa integrando três
componentes com funções distintas e complementares:

| Componente | Função | Natureza |
| --- | --- | --- |
| **AHP** (*Analytic Hierarchy Process*) | Pesos relativos das três dimensões, a partir das comparações par a par do gestor | Determinístico |
| **RAG** (*Retrieval-Augmented Generation*) | Recuperação das evidências documentais dos provedores, indicador a indicador | Recuperação |
| **LLM** (*Large Language Model*) | Extração, interpretação e contextualização das evidências recuperadas | Probabilístico |
| **Regras determinísticas** | Pesos locais, normalização, agregação, ranking | Determinístico |

A separação é a característica central da arquitetura: **a LLM não atribui
pontuação, peso nem posição**. Ela lê documentos e devolve o valor publicado ou a
categoria de rubrica correspondente; a conversão em número, a ponderação e a
ordenação permanecem em código determinístico, reprodutível e auditável.

---

## 2. Objetivo e funcionamento

### 2.1 Objetivo

Apoiar a avaliação técnico-estratégica de provedores de Cloud Computing de forma
estruturada, transparente e rastreável, preservando o gestor como responsável
pela decisão final. A plataforma não substitui a análise especializada, os
procedimentos formais de contratação pública nem a avaliação
econômico-orçamentária das alternativas.

### 2.2 Fluxo de funcionamento

1. **Coleta das prioridades.** O gestor responde a um questionário de 25
   perguntas em cinco blocos (Seção 2.3).

2. **Pesos das dimensões (AHP).** As comparações par a par do Bloco D são
   convertidas para a Escala Fundamental de Saaty, montam a matriz recíproca e
   produzem o vetor de prioridades, com λmax, índice de consistência (IC) e razão
   de consistência (CR).

   **Com CR acima de 0,10 a avaliação não prossegue.** O sistema informa a
   inconsistência, indica as comparações a revisar e o par cujo julgamento mais
   destoa dos demais, e devolve o gestor ao Bloco D com as respostas preservadas.
   A verificação ocorre antes de qualquer chamada à LLM.

3. **Pesos locais dos indicadores.** As respostas de relevância dos Blocos A, B e
   C são convertidas, por regra determinística, em coeficientes normalizados
   dentro de cada dimensão. O peso global de cada indicador é o produto entre o
   peso da sua dimensão (AHP) e o seu peso local.

4. **Recuperação das evidências (RAG).** Para cada par (provedor × indicador), a
   plataforma monta uma consulta a partir dos termos associados àquele indicador
   e recupera os trechos semanticamente mais próximos na base vetorial,
   restritos aos documentos do provedor em questão.

5. **Extração das evidências (LLM).** Os trechos recuperados são submetidos ao
   modelo de linguagem sob um prompt estruturado que fixa os indicadores, proíbe
   a criação de critérios e veda a atribuição de pontuação. A saída é validada
   contra um esquema: cada evidência traz o indicador analisado, a natureza
   (quantitativa, qualitativa ou insuficiente), o valor ou característica
   extraída, a síntese e a referência ao trecho de origem.

6. **Tratamento e normalização.** Indicadores quantitativos são normalizados por
   benefício (valor ÷ maior valor) ou por minimização (menor valor ÷ valor).
   Indicadores qualitativos são convertidos pela rubrica de nível de atendimento.
   Indicador sem evidência comparável em **todos** os provedores sai da avaliação
   daquela execução — para todas as alternativas — e os pesos dos indicadores
   restantes são renormalizados para somar 1. Ausência de evidência nunca vira
   pontuação zero.

7. **Agregação e ranking.** A pontuação global de cada provedor é a soma
   ponderada dos desempenhos normalizados pelos pesos efetivos. As alternativas
   são ordenadas em ordem decrescente, com empates exibidos como empates.

8. **Apresentação.** O relatório traz o ranking, os pesos das dimensões, os pesos
   locais e efetivos dos indicadores, os desempenhos normalizados, a contribuição
   de cada indicador para a pontuação, as evidências documentais recuperadas com
   as respectivas fontes e a justificativa textual das prioridades.

### 2.3 Questionário e o destino de cada bloco

| Bloco | Questões | Tipo | Destino no modelo |
| --- | --- | --- | --- |
| **A** — Sustentabilidade | 1–5 | Relevância (5 níveis) | Coeficientes → pesos locais dos indicadores da dimensão |
| **B** — Desempenho operacional | 7–10 | Relevância (5 níveis) | Coeficientes → pesos locais dos indicadores da dimensão |
| **C** — Segurança da informação | 12–15 | Relevância (5 níveis) | Coeficientes → pesos locais dos indicadores da dimensão |
| **D** — Comparações par a par | 17–19 | Dimensão prioritária + intensidade | **Única fonte dos pesos entre as dimensões** (AHP) |
| **E** — Requisitos institucionais | 20–25 | Dissertativas | Refinamento das consultas do RAG e contexto da justificativa |
| A, B, C | 6, 11, 16 | Dissertativas | Refinamento das consultas do RAG e contexto da justificativa |

Duas separações são deliberadas e não devem ser confundidas:

- **A escala 1–5 dos Blocos A/B/C não é a escala de Saaty.** A primeira mede a
  relevância individual de um indicador dentro de uma dimensão; a segunda mede a
  preferência relativa entre duas dimensões. Converter uma na outra produziria
  julgamentos que o gestor nunca informou.

- **O Bloco E não altera peso algum.** Suas respostas são interpretadas pela LLM
  e associadas aos indicadores já definidos, contribuindo apenas para direcionar
  a recuperação e a interpretação das evidências documentais.

No Bloco D, o gestor informa **qual dimensão prioriza** e **com que intensidade
verbal** (moderadamente, fortemente, muito fortemente, extremamente) ou declara
igual importância. A razão de Saaty correspondente é derivada no servidor; não há
peso numérico trafegando na requisição, de modo que o cliente não tem como
enviar um valor pronto.

### 2.4 Base documental e RAG

A base de conhecimento é alimentada por duas fontes, com papéis distintos:

1. **Documentos do administrador (globais).** Ficam em `data/pdf` e são
   indexados pela área de gestão com escopo global. São consultados em todas as
   buscas, independentemente do usuário.

2. **Documentos do usuário (por sessão).** Enviados pela interface de anexos,
   ficam em `data/upload/<session_id>` e são indexados com escopo restrito à
   avaliação correspondente. São consultados apenas quando a sessão está ativa,
   e não são incorporados à base global — o isolamento evita que um documento de
   uma avaliação contamine outra.

A ingestão compreende leitura, segmentação em trechos, vetorização e indexação.
Cada trecho carrega identidade e procedência determinísticas (identificador do
trecho, do documento, hash de conteúdo, nome do arquivo, página, ano, escopo e
data de ingestão), de modo que reindexar o mesmo arquivo produz os mesmos
identificadores e uma evidência registrada continua apontando para o mesmo
trecho.

Um provedor sem documentos indexados fica **fora da comparação**, e a ausência é
declarada no relatório. Ele não recebe nota de reserva.

---

## 3. Arquitetura do sistema

Arquitetura web desacoplada, em dois serviços conteinerizados:

- **Frontend (SPA).** Coleta do questionário, envio de documentos,
  acompanhamento do processamento e visualização dos resultados. Páginas de
  questionário, resultados, ingestão de documentos e administração.

- **Backend (API REST).** Orquestra o fluxo: processamento do questionário,
  execução do AHP, recuperação documental, integração com os modelos de
  linguagem, aplicação das regras determinísticas, persistência e auditoria.

A comunicação é HTTP/JSON. Em produção, o servidor web do contêiner de frontend
faz o *proxy* de `/api` para o backend, de modo que as duas pontas respondem na
mesma origem.

---

## 4. Tecnologias utilizadas

### 4.1 Frontend

- **React** com **TypeScript** — interfaces baseadas em componentes reutilizáveis
  e tipagem estática durante o desenvolvimento.
- **Vite** — construção e empacotamento da aplicação.
- **Tailwind CSS** — composição visual, complementada por variáveis e *design
  tokens* próprios para padronização da interface e diferenciação visual dos
  provedores.
- **React Router** — navegação entre as funcionalidades, mantendo o comportamento
  de *Single Page Application*.
- **Recharts** — representação gráfica dos resultados e dos painéis.
- **Context API** — gerenciamento de estado.
- **`fetch`** — comunicação com o backend, encapsulada em uma camada de acesso
  aos serviços que reduz o acoplamento entre componentes visuais e API.

O questionário é externalizado em arquivo de configuração JSON, permitindo
alterar questões, textos e parâmetros sem reconstruir a aplicação. Em ambiente
conteinerizado, o arquivo é disponibilizado por volume.

### 4.2 Backend

- **Python** com **FastAPI** e servidor ASGI **Uvicorn**.
- **Pydantic** — definição e validação das estruturas de entrada e saída.

### 4.3 Inteligência Artificial Generativa e RAG

- **LangChain** — integração com modelos de linguagem, processamento textual e
  recuperação de informações.
- **Provedores de LLM intercambiáveis** — a seleção é feita por configuração
  externa, contemplando serviços comerciais e modelos executados localmente. A
  troca não exige alteração da lógica da aplicação, o que reduz a dependência de
  um fornecedor específico.
- **Embeddings configuráveis** — por serviço externo ou por biblioteca local
  (*Hugging Face* / *Sentence Transformers*), em configuração independente da
  escolha do LLM.
- **FAISS** — indexação e busca por similaridade semântica. O índice é persistido
  localmente e reutilizado entre execuções.
- **Segmentação** — arquivos PDF e textuais são lidos por componentes do
  LangChain e segmentados com `RecursiveCharacterTextSplitter`.

Três prompts versionados compõem o módulo generativo, e cada execução registra
qual deles produziu o quê:

| Prompt | Função |
| --- | --- |
| Extração de evidências | Prompt principal: lê os trechos recuperados e extrai a evidência de cada indicador |
| Refinamento de consultas | Auxiliar: associa os requisitos institucionais do gestor aos indicadores, produzindo termos de busca |
| Justificativa das preferências | Auxiliar: redige a explicação textual das prioridades declaradas |

### 4.4 Modelo multicritério

- **NumPy** — construção da matriz de comparação par a par, cálculo do vetor de
  prioridades, da razão de consistência, da normalização dos desempenhos e da
  agregação ponderada.

Os procedimentos quantitativos permanecem separados do processamento realizado
pelos modelos de linguagem, preservando a reprodutibilidade dos cálculos.

### 4.5 Persistência

- **SQLAlchemy** como camada de mapeamento objeto-relacional, sobre **SQLite**.
  A abstração permite substituir o banco por outro sistema gerenciador, como
  PostgreSQL, alterando apenas a configuração de conexão.

### 4.6 Controle de acesso

- Autenticação das funcionalidades administrativas por **token assinado com
  HMAC-SHA256** e esquema **HTTP Bearer**.
- **Limitação de requisições por endereço IP** nas rotas de autenticação.

### 4.7 Qualidade e implantação

- **pytest** — suíte de testes automatizados do backend, cobrindo as regras de
  negócio, o método multicritério, os guardrails, a extração de evidências e a
  integração do endpoint.
- **Docker** e **Docker Compose** — dois serviços isolados em contêineres, com
  mapeamento de portas, volumes persistentes, verificações de integridade e
  dependências entre contêineres. A construção do frontend usa múltiplos
  estágios, separando o ambiente de compilação do de execução, no qual os
  arquivos estáticos são servidos por **Nginx**.
- **Variáveis de ambiente** em arquivos `.env`, com arquivo de exemplo
  documentando os parâmetros esperados, evitando credenciais no código-fonte.

---

## 5. Rastreabilidade, segurança e controle das respostas

A plataforma aplica mecanismos de proteção e validação (*guardrails*) como camada
transversal, implementados em código próprio e não delegados a biblioteca
externa.

**Na entrada.** Limite de tamanho das respostas abertas; varredura de credenciais
e chaves de acesso, com mascaramento ou recusa; heurísticas de detecção de
tentativas de manipulação de instruções. O texto do gestor entra no prompt
encapsulado em marcação própria, e as instruções do sistema declaram que aquele
conteúdo é dado, nunca comando.

**Nos documentos.** Arquivos enviados são validados por formato, tamanho,
assinatura real do conteúdo (e não apenas pela extensão) e possibilidade de
extração textual, com nome saneado e caminho contido. Os trechos recuperados
também entram no prompt encapsulados como dado.

**Na saída.** As respostas do modelo são validadas contra esquemas previamente
definidos. Evidência que cite um trecho fora do contexto entregue, categoria fora
da rubrica do indicador ou indicador fora da lista fornecida são recusadas e
registradas — nunca aproveitadas como valor aproximado. Quando a informação não
está presente nos documentos, o modelo registra a ausência em vez de preencher a
lacuna com conhecimento próprio.

**No registro.** Cada avaliação grava, em tabelas próprias: o envio e os payloads
íntegros de entrada e saída; uma linha por resposta, com o enunciado como estava
no momento do envio; as comparações par a par com a alternativa escolhida; o
ranking com a contribuição por critério; cada execução de modelo de linguagem
(prompt, versão, provedor, modelo, estado, latência, tokens); cada evento de
guardrail, sempre com a amostra mascarada; cada consulta ao RAG e os trechos
devolvidos; os documentos ingeridos; e os pesos de cada indicador nos quatro
níveis. O identificador do registro é também o identificador de rastreio da
execução e aparece no rodapé do relatório.

**Nas versões.** Cada avaliação registra com que questionário (e respectivo
*hash*), algoritmo, prompts, modelos e configuração metodológica foi produzida.
Alterar um coeficiente ou um enunciado muda a versão sem reescrever o passado.

A separação entre componentes probabilísticos e determinísticos é, ela própria,
o principal mecanismo de controle: o modelo de linguagem não tem autonomia para
alterar critérios, pesos ou regras matemáticas, nem para selecionar o provedor
recomendado. Os esquemas de saída não possuem campo onde caiba uma pontuação.

---

## 6. Funcionalidades disponibilizadas

- **Relatório da recomendação** com ranking, pesos das dimensões, desempenho por
  dimensão, evidências documentais agrupadas por provedor e justificativa
  textual. Cada evidência liga ao documento de origem, na página citada.
- **Memória de cálculo do AHP** — matriz de comparação, matriz normalizada, o
  julgamento como o gestor o informou, λmax, IC, IR e CR.
- **Memória de cálculo da agregação** — para cada par (provedor × indicador): o
  valor publicado no documento, a unidade ou o nível de atendimento, o estado da
  evidência, a fonte, o valor normalizado, o peso efetivo e a contribuição. Os
  indicadores excluídos aparecem com o motivo da exclusão.
- **Explicação por contribuição** — decomposição da pontuação por dimensão e por
  indicador, distinta da justificativa textual produzida pela LLM.
- **Exportação do relatório** para registro externo e compartilhamento.
- **Área administrativa autenticada** — consulta dos questionários preenchidos e
  dos resultados, indicadores agregados, gráficos, tabela paginada com busca e
  exportação em CSV.
- **Gestão da base documental** — inventário que cruza o diretório do servidor,
  os documentos já ingeridos e o índice vetorial, com ingestão total ou por
  arquivo selecionado.

### Limitações declaradas

- A versão avaliada **não contempla análise de sensibilidade** dos pesos.
- Provedores sem documentos indexados ficam fora da comparação.
- Indicadores sem evidência comparável em todas as alternativas saem da avaliação
  daquela execução, o que reduz a base do ranking — a proporção é informada.
- Os mecanismos de guardrail mitigam, mas não eliminam, os riscos inerentes ao
  uso de modelos generativos.

---

## 7. Componentes principais

| Componente | Função principal | Tecnologia |
| --- | --- | --- |
| Interface web | Questionário, envio de documentos, relatório e painéis | React, TypeScript, Vite, Tailwind, Recharts |
| API REST | Orquestração do fluxo e exposição dos endpoints | FastAPI, Uvicorn, Pydantic |
| Pesos das dimensões | Matriz par a par, vetor de prioridades, razão de consistência | NumPy |
| Pesos dos indicadores | Coeficientes de relevância → peso local → peso global → peso efetivo | Regras determinísticas |
| Recuperação documental | Consulta por indicador e provedor na base vetorial | LangChain, FAISS, embeddings |
| Extração de evidências | Leitura dos trechos e extração estruturada por indicador | LangChain, provedor de LLM configurável |
| Normalização e agregação | Valor comparável, conjunto válido, pontuação e ranking | NumPy |
| Guardrails | Validação de entrada, de documentos e de saída; registro auditável | Código próprio |
| Persistência e auditoria | Envios, cálculos, execuções de modelo, consultas e evidências | SQLAlchemy, SQLite |
| Implantação | Execução reproduzível dos dois serviços | Docker, Docker Compose, Nginx |

---

## 8. Considerações para a dissertação

Este resumo descreve o **produto tecnológico** desenvolvido: uma plataforma web
que integra questionário estruturado, método AHP, recuperação documental (RAG) e
modelos de linguagem em um fluxo único de apoio à decisão, implantada em
contêineres.

O ponto que o texto da dissertação deve preservar ao incorporar este material é a
**delimitação de responsabilidades entre os componentes**: o AHP representa as
preferências entre as dimensões e calcula seus pesos; as regras determinísticas
transformam o perfil de relevância em pesos locais e convertem as evidências em
valores comparáveis; o RAG recupera e dá rastreabilidade às evidências; e a LLM
auxilia na extração, interpretação e contextualização das informações
documentais, sem participar da ponderação, da normalização, da agregação ou da
escolha do provedor.

O documento pode ser incorporado na seção destinada à descrição do sistema, do
seu funcionamento e da *stack* tecnológica do produto.

### Divergências conhecidas em relação ao texto atual da dissertação

Registradas aqui para conferência, não como correções aplicadas ao texto:

- A **Seção 5.3** cita "NumPy e Pandas" nos procedimentos quantitativos. O Pandas
  foi removido quando a agregação passou a ser a soma ponderada da Equação 5; a
  aritmética hoje é só de vetores NumPy.
- Os **Quadros 22 e 24** não descrevem o conjunto de indicadores efetivamente
  avaliado. A relação entre os quadros e os treze indicadores operacionais, com o
  motivo de cada exclusão, é gerada por `scripts/generate_quadros.py`.
- A **Seção 4.2.3** descreve o bloqueio por inconsistência do AHP; o produto o
  implementa, e acrescenta a indicação do par de comparações que mais destoa —
  recurso não previsto no texto.
- A **Seção 4.4.1** prevê três situações para a evidência (quantitativa,
  qualitativa e ausente). O produto trabalha com quatro estados, acrescentando
  `PARTIAL` para o trecho que trata do tema sem sustentar o valor pedido; por
  decisão de configuração, ele fica fora do conjunto comparável.
