import { useEffect, useState } from "react";

const STAGES = [
  { icon: "🧠", label: "Interpretando suas respostas com IA" },
  { icon: "⚖️", label: "Calculando pesos e ranking (AHP)" },
  { icon: "📚", label: "Buscando evidências nos relatórios (RAG)" },
  { icon: "📊", label: "Montando o relatório" },
];

/**
 * Overlay de progresso do /api/recommend. As etapas avançam por tempo estimado —
 * o backend responde só no fim, então isto comunica o que está acontecendo, não
 * um progresso real medido.
 */
export default function LoadingOverlay({ open }: { open: boolean }) {
  const [stage, setStage] = useState(0);

  useEffect(() => {
    if (!open) {
      setStage(0);
      return;
    }
    const timers = [
      setTimeout(() => setStage(1), 2500),
      setTimeout(() => setStage(2), 6000),
      setTimeout(() => setStage(3), 11000),
    ];
    return () => timers.forEach(clearTimeout);
  }, [open]);

  if (!open) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/60 backdrop-blur-sm px-4 animate-[fadeIn_.2s_ease-out]"
    >
      <div className="w-full max-w-sm rounded-2xl bg-white p-8 shadow-2xl ring-1 ring-slate-900/5">
        <div className="flex justify-center mb-6">
          <div className="relative h-14 w-14">
            <div className="absolute inset-0 rounded-full border-4 border-slate-200" />
            <div className="absolute inset-0 rounded-full border-4 border-transparent border-t-blue-600 animate-spin" />
          </div>
        </div>

        <h2 className="text-center text-base font-bold text-slate-900 mb-1">
          Gerando sua recomendação
        </h2>
        <p className="text-center text-xs text-slate-500 mb-6">
          Isso costuma levar de 5 a 20 segundos.
        </p>

        <ul className="space-y-2.5">
          {STAGES.map((s, i) => {
            const done = i < stage;
            const active = i === stage;
            return (
              <li
                key={s.label}
                className={
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-all " +
                  (active
                    ? "bg-blue-50 text-blue-900 font-medium"
                    : done
                      ? "text-slate-400"
                      : "text-slate-300")
                }
              >
                <span className="text-base leading-none w-5 text-center">
                  {done ? "✓" : s.icon}
                </span>
                <span>{s.label}</span>
                {active && (
                  <span className="ml-auto flex gap-1">
                    <Dot delay="0ms" />
                    <Dot delay="150ms" />
                    <Dot delay="300ms" />
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function Dot({ delay }: { delay: string }) {
  return (
    <span
      className="h-1.5 w-1.5 rounded-full bg-blue-500 animate-bounce"
      style={{ animationDelay: delay }}
    />
  );
}
