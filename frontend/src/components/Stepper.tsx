const STEPS = [
  { key: "home", label: "Início" },
  { key: "questionnaire", label: "Questionário" },
  { key: "results", label: "Relatório" },
] as const;

type StepKey = (typeof STEPS)[number]["key"];

export default function Stepper({ current }: { current: StepKey }) {
  const currentIndex = STEPS.findIndex((s) => s.key === current);

  return (
    <div className="flex items-center justify-center gap-2 flex-wrap mb-7">
      {STEPS.map((step, i) => {
        const isActive = step.key === current;
        const isDone = i < currentIndex;
        return (
          <div key={step.key} className="flex items-center gap-2">
            <span
              className={
                "flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[13px] font-semibold transition-colors " +
                (isActive
                  ? "bg-gradient-to-br from-blue-600 to-indigo-600 text-white shadow-[0_2px_10px_rgba(37,99,235,0.35)]"
                  : isDone
                    ? "bg-green-100 text-green-700"
                    : "bg-slate-200 text-slate-500")
              }
            >
              {isDone ? "✓" : `${i + 1}.`} {step.label}
            </span>
            {i < STEPS.length - 1 && <span className="text-slate-300">→</span>}
          </div>
        );
      })}
    </div>
  );
}
