export type OptionSet = string[];

export interface QuestionDef {
  id: string;
  type: "choice" | "text";
  label: string;
  options?: string | OptionSet;
  placeholder?: string;
  required?: boolean;
}

export interface SectionDef {
  id: string;
  badge: string;
  title: string;
  questions: QuestionDef[];
}

export interface QuestionsFile {
  option_sets: Record<string, OptionSet>;
  sections: SectionDef[];
}

export interface AnswerPayload {
  question_id: string;
  /** Enunciado, para o LLM interpretar a resposta sabendo o que foi perguntado */
  question_text?: string;
  choice: string | null;
  text: string | null;
}

/** Memória de cálculo do AHP, para o relatório poder ser auditado */
export interface AhpResult {
  criteria_order: string[];
  pairwise_matrix: number[][];
  intensities: Record<string, number>;
  base_scores: Record<string, number>;
  comparative_adjustments: Record<string, number>;
  llm_adjustments: Record<string, number>;
  lambda_max: number;
  consistency_index: number;
  consistency_ratio: number;
  is_consistent: boolean;
  consistency_threshold: number;
}

export interface RecommendPayload {
  respondent: string;
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
  unscored_answers?: string[];
  coverage?: Coverage;
}

/** Cobertura documental do ranking e procedência das notas dos provedores */
export interface Coverage {
  evaluated: { id: string; name: string; chunks: number }[];
  excluded_no_documents: { id: string; name: string }[];
  scores_provenance: { status: string; summary: string };
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
