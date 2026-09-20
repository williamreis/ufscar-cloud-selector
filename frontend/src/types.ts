export type OptionSet = string[];

/** Intensidade verbal da preferência. A conversão para 3/5/7/9 é do backend. */
export type PairwiseIntensity = "moderate" | "strong" | "very_strong" | "extreme";

/** Dimensão priorizada (id do critério) ou indiferença. */
export type PairwisePreference = string | "equal";

/** Par comparado por uma questão do tipo "pairwise" */
export interface PairDef {
  left: string;
  right: string;
}

/** Declaração de como a resposta é processada (documental; o cálculo é por tipo) */
export interface ProcessingDef {
  purposes?: string[];
  ahp_input?: boolean;
  llm_interpretation?: boolean;
}

export interface QuestionDef {
  id: string;
  type: "choice" | "text" | "pairwise";
  label: string;
  options?: string | OptionSet;
  placeholder?: string;
  required?: boolean;
  /** Só em type "pairwise": as duas dimensões comparadas */
  pair?: PairDef;
  processing?: ProcessingDef;
}

export interface SectionDef {
  id: string;
  badge: string;
  title: string;
  description?: string;
  questions: QuestionDef[];
}

/** Rótulo de exibição de uma dimensão do AHP */
export interface DimensionDef {
  label: string;
  /** Versão curta, para caber no cartão da comparação */
  short?: string;
  icon?: string;
}

export interface QuestionsFile {
  option_sets: Record<string, OptionSet>;
  /** Rótulos das dimensões referenciadas por `pair.left` / `pair.right` */
  dimensions?: Record<string, DimensionDef>;
  sections: SectionDef[];
}

/**
 * Comparação par a par como o gestor a respondeu.
 *
 * Não carrega razão de Saaty nem valor de matriz de propósito: quem converte é o
 * backend (`pairwise.py`), então não há número de peso trafegando no payload.
 */
export interface PairwiseAnswer {
  left: string;
  right: string;
  preference: PairwisePreference;
  /** Nula — e obrigatoriamente nula — quando a preferência é "equal" */
  intensity: PairwiseIntensity | null;
}

export interface AnswerPayload {
  question_id: string;
  /** Enunciado, para o LLM interpretar a resposta sabendo o que foi perguntado */
  question_text?: string;
  choice: string | null;
  text: string | null;
  /** Preenchido só nas comparações do bloco D */
  pairwise?: PairwiseAnswer | null;
}

/** Julgamento par a par informado pelo gestor na seção D */
export interface AhpJudgment {
  /** Razão a_ij na escala de Saaty (1, 3, 5, 7, 9 e recíprocos) */
  ratio: number;
  /** Frase legível equivalente ao julgamento, para rastrear a matriz até a resposta */
  choice?: string | null;
  question_id?: string | null;
  /** Dimensão priorizada ou "equal" */
  preference?: string | null;
  /** Intensidade verbal; nula na indiferença */
  intensity?: PairwiseIntensity | null;
  /** Intensidade de Saaty antes da direção (1, 3, 5, 7 ou 9) */
  saaty_intensity?: number | null;
}

/** Memória de cálculo do AHP, para o relatório poder ser auditado */
export interface AhpResult {
  criteria_order: string[];
  pairwise_matrix: number[][];
  /** Chaveado por "<critério A>|<critério B>" */
  judgments: Record<string, AhpJudgment>;
  /** Pares sem julgamento, preenchidos com 1 (indiferença) */
  missing_judgments: string[];
  /** Relevância média dos indicadores (1–5) por dimensão — fora do cálculo dos pesos */
  relevance_by_criterion: Record<string, number>;
  lambda_max: number;
  consistency_index: number;
  consistency_ratio: number;
  is_consistent: boolean;
  consistency_threshold: number;
  /** Preenchido apenas em envios antigos, gravados antes da porta da §4.2.3 */
  worst_pair?: WorstPair | null;
}

/** Estado da evidência de um indicador (§11) */
export type EvidenceStatus = "FOUND" | "PARTIAL" | "NOT_FOUND" | "INVALID";

/** Uma célula (provedor × dimensão) da agregação */
export interface SynthesisCell {
  /** Peso da dimensão vindo do AHP */
  weight: number;
  /**
   * Peso com que a dimensão **efetivamente** entrou na soma: os pesos dos seus
   * indicadores válidos, já renormalizados sobre o conjunto comparável (§11.2).
   * Difere de `weight` sempre que algum indicador é excluído — e é este que
   * fecha a conta.
   */
  effective_weight?: number;
  /** Soma das contribuições dos indicadores da dimensão */
  contribution: number;
  /**
   * Desempenho médio na dimensão (0–1), sem o peso dela. Ausente quando nenhum
   * indicador da dimensão reuniu evidência comparável.
   */
  performance?: number;
}

/** Uma linha da matriz de desempenho: um indicador de um provedor */
export interface SynthesisIndicator {
  indicator_id: string;
  name: string;
  dimension: string;
  data_type: "quantitative" | "qualitative";
  direction: "benefit" | "minimize" | null;
  status: EvidenceStatus;
  nature: string | null;
  /** "o valor ou característica extraída" (§5.4), como o documento a apresenta */
  extracted_value: string | null;
  /** Valor numérico que entrou no cálculo */
  original_value: number | null;
  unit: string | null;
  /** Nível de atendimento do Quadro 23 (baixo, moderado, alto, completo…) */
  category: string | null;
  /** Condição da evidência que justifica o nível, também do Quadro 23 */
  category_condition: string | null;
  /** Síntese da evidência produzida pela LLM */
  summary: string | null;
  /** Por que a evidência foi recusada na validação, quando foi */
  rejection: string | null;
  source_chunk_id: string | null;
  source_document: string | null;
  /** Ano do documento que sustenta a evidência. Duas medições de anos distintos
   *  não são comparáveis, e o gestor precisa ver de que período é cada número. */
  source_year: number | null;
  /** Entrou no conjunto comparável V (§11.1) */
  in_comparison: boolean;
  excluded_reason: string | null;
  /** r_ij — Equações 1 e 2 */
  normalized_value: number | null;
  /** w'_j — peso global renormalizado sobre V (§11.2) */
  effective_weight: number | null;
  /** w'_j × r_ij */
  contribution: number | null;
}

/** Memória de cálculo do score final (Equação 5: soma ponderada) */
export interface SynthesisResult {
  mode: string;
  equation: string;
  criteria_order: string[];
  /** Pesos das dimensões, do AHP — o que o gestor declarou */
  dimension_weights: Record<string, number>;
  /**
   * Pesos das dimensões como entraram na soma, após a renormalização da §11.2.
   * Opcional: relatórios gravados antes deste campo não o têm, e a área de
   * gestão os reexibe a partir do `response_json` original.
   */
  dimension_effective_weights?: Record<string, number>;
  /** Pesos dos indicadores já renormalizados sobre o conjunto comparável */
  effective_weights: Record<string, number>;
  valid_indicators: string[];
  /** indicator_id → motivo da exclusão */
  excluded_indicators: Record<string, string>;
  indicators: {
    indicator_id: string;
    name: string;
    dimension: string;
    data_type: string;
    direction: string | null;
    effective_weight: number;
  }[];
  providers: {
    id: string;
    name: string;
    rank: number;
    tied: boolean;
    cells: Record<string, SynthesisCell>;
    indicators: SynthesisIndicator[];
    score: number;
  }[];
  tie_break_policy: string;
  has_ties: boolean;
  /** Soma dos pesos efetivos — deve ser 1, serve de verificação */
  effective_weight_sum: number;
  /** Fração dos indicadores com peso que entraram na comparação */
  comparability_rate: number | null;
}

/** Os quatro níveis de peso de um indicador (§7 e §11.2) */
export interface IndicatorWeightRow {
  indicator_id: string;
  name: string;
  dimension: string;
  question_id: string | null;
  relevance_coefficient: number | null;
  relevance_state: "answered" | "unknown" | "missing";
  local_weight: number | null;
  dimension_weight: number | null;
  global_weight: number | null;
  effective_weight: number | null;
  is_valid_for_comparison: boolean;
  excluded_reason: string | null;
}

export interface IndicatorWeights {
  indicators: IndicatorWeightRow[];
  dimensions_needing_review: string[];
  global_weight_sum: number;
  effective_weight_sum: number;
  /** "evidence_extraction" desde que o desempenho passou a vir dos documentos */
  performance_source: string;
}

/**
 * Par de comparações cujo julgamento mais destoa dos demais.
 *
 * `judged_ratio` é o que o gestor informou; `implied_ratio` é o que as outras
 * comparações, juntas, implicam para o mesmo par. A distância entre os dois é a
 * contradição.
 */
export interface WorstPair {
  pair: string;
  left: string;
  right: string;
  judged_ratio: number;
  implied_ratio: number;
  log_deviation: number;
}

/** Uma alteração proposta para uma comparação do bloco D. */
export interface SuggestedChange {
  pair: string;
  question_id: string | null;
  left: string;
  right: string;
  from: { preference: string | null; intensity: string | null; description: string };
  to: { preference: string | null; intensity: string | null; description: string };
  steps: number;
  /**
   * "intensity" mantém a dimensão priorizada e só muda a força; "preference" cria
   * ou abandona uma preferência; "inversion" troca quem vence — o único que
   * contraria o que o gestor declarou, e por isso vem rotulado na tela.
   */
  kind: "intensity" | "preference" | "inversion" | "unchanged";
}

/** Uma revisão completa que traria o CR para dentro do limite. */
export interface RevisionOption {
  consistency_ratio: number;
  changed_comparisons: number;
  inverts_preference: boolean;
  changes: SuggestedChange[];
}

/**
 * Caminhos de revisão devolvidos com o 409.
 *
 * São propostas, não correções: o backend não altera nada do que foi enviado.
 * A lista traz uma opção por comparação justamente para que quem escolhe de qual
 * julgamento abrir mão seja o gestor, e não o sistema.
 */
export interface ConsistencySuggestion {
  /**
   * Preenchido quando as preferências formam um ciclo (A > B > C > A). Aí não é
   * questão de calibrar intensidade: nenhuma ordem de prioridade satisfaz as três.
   */
  cycle: { dimensions: string[]; statements: string[] } | null;
  options: RevisionOption[];
}

/** Corpo do 409 quando as comparações do bloco D se contradizem (§4.2.3) */
export interface InconsistencyDetail {
  error: "AHP_INCONSISTENT_JUDGMENTS";
  message: string;
  consistency_ratio: number;
  consistency_threshold: number;
  consistency_index: number;
  lambda_max: number;
  /** Perguntas do bloco D a revisar */
  question_ids: string[];
  judgments: Record<string, { choice: string | null; question_id: string | null }>;
  worst_pair: WorstPair | null;
  suggestion: ConsistencySuggestion | null;
}

export interface RecommendPayload {
  /** E-mail do respondente */
  respondent: string;
  /** Cargo/função do respondente na instituição */
  respondent_role?: string;
  answers: AnswerPayload[];
  session_id: string;
}

export interface RankingRow {
  id: string;
  name: string;
  rank: number;
  score: number;
  /**
   * Pontuação igual à de outro provedor, dentro da tolerância de desempate.
   * Vem do backend (`show_tie`, §12) e precisa chegar até a tela: sem ele o
   * relatório ordena por posição na lista e apresenta como decidido o que o
   * cálculo deixou empatado.
   */
  tied: boolean;
}

export interface ProviderScoreRow extends RankingRow {
  sustainability?: number;
  performance?: number;
  security?: number;
  // Dimensão sem indicador válido vem **ausente** do backend, nunca como zero
  // (§11) — por isso as três são opcionais e o índice admite `undefined`.
  // `boolean` entra por causa de `tied`, herdado de RankingRow.
  [criterion: string]: string | number | boolean | undefined;
}

export interface EvidenceItem {
  page_content: string;
  score: number;
  criterion?: string;
  /** Indicador cuja consulta recuperou este trecho */
  indicator_id?: string;
  indicator_name?: string;
  /**
   * Termos do Quadro 27 (do indicador acima) que aparecem neste trecho.
   * Vazio quando o trecho veio por similaridade, sem casar termo à letra.
   */
  matched_terms?: string[];
  file_name?: string | null;
  /** 1-indexed, pronto para exibição e para o fragmento #page=N do viewer de PDF */
  page?: number | null;
  page_label?: string | null;
  total_pages?: number | null;
  /** "global" (data/pdf) ou "session" (upload do usuário) */
  scope?: string | null;
  session_id?: string | null;
}

export interface RecommendationResponse {
  ranking: RankingRow[];
  criteria_weights: Record<string, number>;
  provider_scores: ProviderScoreRow[];
  notes: string;
  evidences: Record<string, EvidenceItem[]>;
  ahp?: AhpResult;
  synthesis?: SynthesisResult;
  sensitivity?: SensitivityResult | null;
  indicator_weights?: IndicatorWeights;
  status?: string;
  limitations?: string[];
  /** Id do registro de auditoria. Nulo = o envio não foi gravado no banco. */
  submission_id?: string | null;
  unscored_answers?: string[];
  coverage?: Coverage;
}

/** Quanto o peso de uma dimensão precisa mudar para o líder deixar de liderar */
export interface DimensionSensitivity {
  dimension: string;
  weight: number;
  /** Algum peso em [0,1] dessa dimensão troca o primeiro colocado */
  flips: boolean;
  /** Menor deslocamento de peso que troca o líder, em pontos (0,08 = 8 p.p.) */
  flip_delta: number | null;
  /** Peso da dimensão no ponto de virada */
  flip_weight: number | null;
  /** Quem passa a liderar ali */
  flip_leader: string | null;
}

/**
 * Robustez do 1º lugar.
 *
 * Mede o resultado, não entra nele: nenhum campo daqui altera pontuação, peso ou
 * posição. Existe porque uma liderança de 0,002 e uma de 0,20 são exibidas com a
 * mesma firmeza, e só esta seção distingue as duas.
 */
export interface SensitivityResult {
  leader: string;
  /** Distância entre o 1º e o 2º colocados, na escala da pontuação */
  margin: number;
  /** A margem cabe dentro da margem de indiferença do scales.json */
  margin_within_tolerance: boolean;
  tie_break_tolerance: number;
  /** Nenhum peso de dimensão, sozinho, troca o primeiro colocado */
  robust: boolean;
  most_fragile_dimension: string | null;
  most_fragile_delta: number | null;
  dimensions: DimensionSensitivity[];
}

/** Cobertura documental do ranking e procedência das notas dos provedores */
export interface Coverage {
  evaluated: { id: string; name: string; chunks: number }[];
  excluded_no_documents: { id: string; name: string }[];
  scores_provenance: { status: string; summary: string };
  /** Cobertura da extração documental (§29) */
  evidence?: {
    by_status: Partial<Record<EvidenceStatus, number>>;
    indicators_requested: number;
    indicators_in_comparison: number;
    excluded_indicators: Record<string, string>;
    comparability_rate: number | null;
  };
}

// ===========================================================================
// Área de gestão / auditoria
// ===========================================================================

export interface SubmissionListItem {
  id: string;
  created_at: string;
  respondent_email: string;
  respondent_role: string | null;
  weights: Record<string, number | null>;
  consistency_ratio: number | null;
  is_consistent: boolean | null;
  top_provider_id: string | null;
  top_provider_name: string | null;
  top_provider_score: number | null;
}

export interface SubmissionDetail extends SubmissionListItem {
  session_id: string | null;
  relevance: Record<string, number | null>;
  lambda_max: number | null;
  consistency_index: number | null;
  llm_notes: string | null;
  llm_provider: string | null;
  llm_model: string | null;
  answers: {
    position: number;
    question_id: string;
    question_text: string | null;
    choice: string | null;
    text: string | null;
  }[];
  judgments: {
    question_id: string | null;
    criterion_a: string;
    criterion_b: string;
    ratio: number;
    choice: string | null;
  }[];
  ranking: {
    provider_id: string;
    provider_name: string;
    rank: number;
    score: number;
    contributions: Record<string, number>;
  }[];
  /** Payload íntegro devolvido pela API na época — reexibe o relatório original */
  response_json: RecommendationResponse;
}

export interface AdminStats {
  total: number;
  average_weights: Record<string, number | null>;
  average_relevance: Record<string, number | null>;
  consistency: {
    consistent: number;
    inconsistent: number;
    average_ratio: number | null;
  };
  top_provider_counts: { id: string; name: string; count: number }[];
  submissions_by_day: { day: string; count: number }[];
  roles: { role: string; count: number }[];
}

export interface IngestResult {
  chunks: number;
  files_processed: number;
  files_failed?: number;
  message?: string;
  details: { file: string; chunks: number }[];
  errors: string[];
  /** Eventos de guardrail da ingestão (arquivo recusado, credencial mascarada). */
  guardrail_events?: { rule_id: string; action: string; reason: string; target?: string | null }[];
  unassigned_files?: string[];
}

/** Um arquivo em data/pdf, cruzado com o que já foi ingerido (GET /api/admin/rag/status). */
export interface RagFile {
  name: string;
  size: number;
  /** epoch em segundos, como vem do stat do arquivo */
  modified_at: number;
  document_id: string;
  provider_id: string | null;
  year: number | null;
  indexed: boolean;
  chunks: number | null;
  ingested_at: string | null;
}

/**
 * Ingestão global em curso. Indexar a base leva minutos — mais que o
 * `proxy_read_timeout` do nginx —, então a rota inicia o trabalho e a tela
 * acompanha por polling em vez de esperar a resposta.
 */
export interface RagJob {
  state: "idle" | "running" | "done" | "error";
  job_id: string | null;
  files: string[];
  started_at: string | null;
  finished_at: string | null;
  progress: { done: number; total: number; current: string | null };
  /** Parcial enquanto roda; final ao terminar. */
  result: IngestResult | null;
  error: string | null;
  /** Só quando não havia o que ingerir. */
  message?: string;
}

export interface RagStatus {
  pdf_dir: string;
  allowed_extensions: string[];
  index_ready: boolean;
  job: RagJob;
  embedding_provider: string;
  embedding_model: string;
  /**
   * Modelo que gerou o índice existente, lido do registro dos documentos.
   * Nulo quando não há documento registrado ou quando o índice mistura modelos.
   */
  index_embedding_provider: string | null;
  index_embedding_model: string | null;
  /** O índice foi gerado por outro modelo — nenhuma busca encontra nada. */
  embedding_mismatch: boolean;
  chunk_size: number;
  chunk_overlap: number;
  files: RagFile[];
  pending_files: number;
  unassigned_files: string[];
  documents_indexed: number;
  chunks_total: number;
  /** Trechos que os documentos registrados declaram ter gerado. */
  chunks_registered: number;
  /** Vetores no índice além do declarado — cópias de uma reingestão anterior. */
  chunks_duplicated: number;
  /** O índice tem cópias repetidas: elas disputam as vagas do `top_k`. */
  index_duplicated: boolean;
  providers: { id: string; name: string; chunks: number }[];
}

/** Resultado da limpeza da base vetorial. `chunks_removed` é nulo quando o
 *  índice não pôde sequer ser lido para contar — o caso do modelo trocado. */
export interface RagResetResult {
  index_removed: boolean;
  documents_cleared: number;
  chunks_removed: number | null;
}

export interface UploadedFile {
  name: string;
  size: number;
}
