import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { AnswerPayload, RecommendationResponse } from "./types";

/**
 * UUID v4 com fallback: `crypto.randomUUID` só existe em secure context
 * (HTTPS ou localhost); em produção sob HTTP puro é `undefined`.
 */
function randomUUID(): string {
  const c = globalThis.crypto;
  if (typeof c?.randomUUID === "function") return c.randomUUID();

  const bytes = new Uint8Array(16);
  if (typeof c?.getRandomValues === "function") {
    c.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40; // versão 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80; // variante RFC 4122

  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function getOrCreateSessionId(): string {
  const KEY = "cloud-selector-session-id";
  let id = sessionStorage.getItem(KEY);
  if (!id) {
    id = randomUUID();
    sessionStorage.setItem(KEY, id);
  }
  return id;
}

interface AppState {
  sessionId: string;
  result: RecommendationResponse | null;
  setResult: (r: RecommendationResponse | null) => void;
  /**
   * Respostas do envio que gerou `result`. A API não as devolve — ficam aqui
   * para o relatório poder mostrar a entrada ao lado do resultado.
   */
  answers: AnswerPayload[] | null;
  setAnswers: (a: AnswerPayload[] | null) => void;
}

const AppStateContext = createContext<AppState | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [sessionId] = useState(getOrCreateSessionId);
  const [result, setResult] = useState<RecommendationResponse | null>(null);
  const [answers, setAnswers] = useState<AnswerPayload[] | null>(null);

  const value = useMemo(
    () => ({ sessionId, result, setResult, answers, setAnswers }),
    [sessionId, result, answers],
  );

  return <AppStateContext.Provider value={value}>{children}</AppStateContext.Provider>;
}

export function useAppState(): AppState {
  const ctx = useContext(AppStateContext);
  if (!ctx) throw new Error("useAppState must be used within AppStateProvider");
  return ctx;
}
