import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { AnswerPayload, RecommendationResponse } from "./types";

function getOrCreateSessionId(): string {
  const KEY = "cloud-selector-session-id";
  let id = sessionStorage.getItem(KEY);
  if (!id) {
    id = crypto.randomUUID();
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
