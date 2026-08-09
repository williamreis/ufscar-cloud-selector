import type {
  IngestResult,
  QuestionsFile,
  RecommendationResponse,
  RecommendPayload,
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
  const res = await fetch(`${API_BASE}/documents/ingest-global`, { method: "POST" });
  return asJson<IngestResult>(res);
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
