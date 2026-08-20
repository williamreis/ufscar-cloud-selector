import type {
  AdminStats,
  IngestResult,
  QuestionsFile,
  RagStatus,
  RecommendationResponse,
  RecommendPayload,
  SubmissionDetail,
  SubmissionListItem,
  UploadedFile,
} from "./types";

// Em produção o nginx do container proxeia /api para o backend (mesma origem).
// Em dev, o vite.config.ts proxeia /api para VITE_DEV_BACKEND_URL.
const API_BASE = "/api";

async function asJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Erro ${res.status}: ${text || res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export async function loadQuestions(): Promise<QuestionsFile> {
  const res = await fetch("/questions.json");
  if (!res.ok) throw new Error("Não foi possível carregar questions.json");
  return res.json() as Promise<QuestionsFile>;
}

export async function postRecommend(
  payload: RecommendPayload,
): Promise<RecommendationResponse> {
  const res = await fetch(`${API_BASE}/recommend`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return asJson<RecommendationResponse>(res);
}

export async function uploadDocuments(
  sessionId: string,
  files: File[],
): Promise<{ uploaded: unknown[]; message: string }> {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  const res = await fetch(
    `${API_BASE}/documents/upload?session_id=${encodeURIComponent(sessionId)}`,
    { method: "POST", body: form },
  );
  return asJson(res);
}

export async function ingestSession(sessionId: string): Promise<IngestResult> {
  const res = await fetch(
    `${API_BASE}/documents/ingest?session_id=${encodeURIComponent(sessionId)}`,
    { method: "POST" },
  );
  return asJson<IngestResult>(res);
}

export async function ingestGlobal(): Promise<IngestResult> {
  const res = await fetch(`${API_BASE}/documents/ingest-global`, {
    method: "POST",
    headers: adminHeaders(),
  });
  return asJson<IngestResult>(res);
}

// ===========================================================================
// Área de gestão
//
// O token vive em sessionStorage: sobrevive à navegação entre páginas e some
// quando a aba fecha. Não é "lembrar de mim" — é evitar pedir a senha a cada
// clique dentro do painel.
// ===========================================================================

const TOKEN_KEY = "cloud-selector-admin-token";

export function getAdminToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY);
}

export function setAdminToken(token: string | null): void {
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
}

function adminHeaders(): Record<string, string> {
  const token = getAdminToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/** Erro de autenticação, para a UI voltar à tela de login em vez de mostrar texto cru. */
export class AdminAuthError extends Error {}

async function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}/admin${path}`, {
    ...init,
    // O Authorization é sempre nosso; o que a chamada pedir (o Content-Type de
    // um corpo JSON, por exemplo) entra por cima.
    headers: { ...adminHeaders(), ...((init?.headers as Record<string, string>) || {}) },
  });
  if (res.status === 401) {
    setAdminToken(null);
    throw new AdminAuthError("Sessão expirada. Faça login novamente.");
  }
  return asJson<T>(res);
}

export async function adminLogin(password: string): Promise<void> {
  const res = await fetch(`${API_BASE}/admin/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!res.ok) {
    const detail = await res
      .json()
      .then((d) => d?.detail)
      .catch(() => null);
    throw new Error(detail || `Erro ${res.status}`);
  }
  const data = (await res.json()) as { token: string };
  setAdminToken(data.token);
}

export async function adminSessionValid(): Promise<boolean> {
  if (!getAdminToken()) return false;
  try {
    await adminFetch<{ ok: boolean }>("/session");
    return true;
  } catch {
    return false;
  }
}

export function adminStats(): Promise<AdminStats> {
  return adminFetch<AdminStats>("/stats");
}

export function adminSubmissions(
  limit: number,
  offset: number,
  search: string,
): Promise<{ items: SubmissionListItem[]; total: number }> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (search.trim()) params.set("search", search.trim());
  return adminFetch(`/submissions?${params.toString()}`);
}

export function adminSubmission(id: string): Promise<SubmissionDetail> {
  return adminFetch<SubmissionDetail>(`/submissions/${encodeURIComponent(id)}`);
}

/** Inventário da base documental global: data/pdf × documentos ingeridos × índice. */
export function adminRagStatus(): Promise<RagStatus> {
  return adminFetch<RagStatus>("/rag/status");
}

/**
 * Executa a ingestão dos documentos de data/pdf no índice RAG.
 *
 * Sem `files`, ingere o diretório inteiro; com `files`, apenas os nomes
 * indicados. Reingerir é seguro: o id do documento é o hash do conteúdo, então o
 * mesmo arquivo atualiza a linha existente em vez de duplicá-la.
 */
export function adminRagIngest(files?: string[]): Promise<IngestResult> {
  return adminFetch<IngestResult>("/rag/ingest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ files: files && files.length ? files : null }),
  });
}

/** Exclusão definitiva — não há lixeira no backend. */
export function adminDeleteSubmission(id: string): Promise<{ deleted: string }> {
  return adminFetch(`/submissions/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/**
 * Baixa o CSV. Passa pelo fetch (e não por um <a href>) porque o endpoint exige
 * o header Authorization, que um link comum não carrega.
 */
export async function adminExportCsv(): Promise<void> {
  const res = await fetch(`${API_BASE}/admin/export.csv`, { headers: adminHeaders() });
  if (res.status === 401) {
    setAdminToken(null);
    throw new AdminAuthError("Sessão expirada. Faça login novamente.");
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Erro ${res.status}: ${text || res.statusText}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "questionarios.csv";
  a.click();
  URL.revokeObjectURL(url);
}

/**
 * URL do documento de origem de uma evidência. O fragmento #page=N é entendido
 * pelo viewer de PDF nativo do navegador e abre direto na página do trecho.
 */
export function documentUrl(ev: {
  file_name?: string | null;
  page?: number | null;
  scope?: string | null;
  session_id?: string | null;
}): string | null {
  if (!ev.file_name) return null;
  const params = new URLSearchParams({ name: ev.file_name });
  if (ev.scope) params.set("scope", ev.scope);
  if (ev.session_id) params.set("session_id", ev.session_id);
  const fragment = ev.page ? `#page=${ev.page}` : "";
  return `${API_BASE}/documents/file?${params.toString()}${fragment}`;
}

export async function listUploaded(sessionId: string): Promise<UploadedFile[]> {
  const res = await fetch(
    `${API_BASE}/documents/uploaded?session_id=${encodeURIComponent(sessionId)}`,
  );
  const data = await asJson<{ files: UploadedFile[] }>(res);
  return data.files || [];
}
