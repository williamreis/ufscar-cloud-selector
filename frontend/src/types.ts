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
}

/** Uma célula (provedor × critério) da síntese das alternativas */
export interface SynthesisCell {
  /** Nota de referência do provedor no critério (0–1) */
  raw: number;
  /** raw ÷ soma das notas do critério */
  normalized: number;
  /** Peso do critério vindo do AHP */
  weight: number;
  /** weight × normalized */
  contribution: number;
}

/** Memória de cálculo do score final (modo distributivo do AHP) */
export interface SynthesisResult {
  mode: string;
  criteria_order: string[];
  weights: Record<string, number>;
  /** Soma das notas de todos os provedores em cada critério (denominador) */
  column_totals: Record<string, number>;
  providers: {
    id: string;
    name: string;
    cells: Record<string, SynthesisCell>;
    score: number;
  }[];
  /** Soma dos scores — deve ser 1, serve de verificação */
  score_total: number;
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
}

export interface ProviderScoreRow extends RankingRow {
  sustainability?: number;
  performance?: number;
  security?: number;
  [criterion: string]: string | number | undefined;
}

export interface EvidenceItem {
  page_content: string;
  score: number;
  criterion?: string;
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
  /** Id do registro de auditoria. Nulo = o envio não foi gravado no banco. */
  submission_id?: string | null;
  unscored_answers?: string[];
  coverage?: Coverage;
}

/** Cobertura documental do ranking e procedência das notas dos provedores */
export interface Coverage {
  evaluated: { id: string; name: string; chunks: number }[];
  excluded_no_documents: { id: string; name: string }[];
  scores_provenance: { status: string; summary: string };
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
}

export interface UploadedFile {
  name: string;
  size: number;
}
