import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import type { RecommendationResponse } from "./types";

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
}

const AppStateContext = createContext<AppState | null>(null);

export function AppStateProvider({ children }: { children: ReactNode }) {
  const [sessionId] = useState(getOrCreateSessionId);
  const [result, setResult] = useState<RecommendationResponse | null>(null);

  const value = useMemo(() => ({ sessionId, result, setResult }), [sessionId, result]);

  return <AppStateContext.Provider value={value}>{children}</AppStateContext.Provider>;
}

export function useAppState(): AppState {
  const ctx = useContext(AppStateContext);
  if (!ctx) throw new Error("useAppState must be used within AppStateProvider");
  return ctx;
}
