/**
 * Uma comparação par a par do bloco D, em duas etapas.
 *
 * Antes cada par listava nove frases completas ("Sustentabilidade é muito
 * fortemente mais importante que Segurança da Informação", e assim por diante) —
 * 27 alternativas no formulário inteiro, quase todas iguais entre si. Aqui a
 * mesma informação sai de duas perguntas curtas: **qual dimensão** tem
 * prioridade e, só então, **com que intensidade**.
 *
 * O componente é o mesmo para os três pares: quem define o que se compara é a
 * questão do questions.json (`pair.left` / `pair.right`), não o código.
 */
import type { PairwiseAnswer, PairwiseIntensity } from "../types";
import { EQUAL, INTENSITIES, isComplete, selectIntensity, selectPreference } from "../pairwise";

interface Props {
  id: string;
  left: { id: string; label: string; icon?: string };
  right: { id: string; label: string; icon?: string };
  value: PairwiseAnswer;
  onChange: (next: PairwiseAnswer) => void;
  /** Posição na sequência de comparações, para o indicador "1 de 3" */
  index: number;
  total: number;
  invalid?: boolean;
}

export default function PairwiseComparison({
  id,
  left,
  right,
  value,
  onChange,
  index,
  total,
  invalid,
}: Props) {
  const complete = isComplete(value);
  const needsIntensity = !!value.preference && value.preference !== EQUAL;
  const preferred = value.preference === left.id ? left : value.preference === right.id ? right : null;
  const other = preferred ? (preferred.id === left.id ? right : left) : null;

  const options = [
    { id: left.id, label: left.label, icon: left.icon || "" },
    { id: EQUAL, label: "Igual importância", icon: "⚖" },
    { id: right.id, label: right.label, icon: right.icon || "" },
  ];

  return (
    <div
      id={`field-${id}`}
      className={
        "scroll-mt-32 rounded-2xl border bg-white shadow-sm transition-all " +
        (invalid ? "border-red-300 ring-4 ring-red-500/10" : "border-slate-200/80")
      }
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-2.5">
        <span className="rounded-md bg-slate-900 px-2 py-0.5 text-[11px] font-bold uppercase tracking-wide text-white">
          Comparação {index} de {total}
        </span>
        <span className="text-xs font-medium text-slate-500">
          {left.label} × {right.label}
        </span>
        {complete && (
          <span className="ml-auto text-xs font-semibold text-emerald-600" aria-hidden>
            ✓ respondida
          </span>
        )}
      </div>

      <div className="p-4">
        {/* Etapa 1 — qual dimensão tem prioridade */}
        <fieldset>
          <legend className="mb-3 flex gap-2 text-sm leading-relaxed text-slate-700">
            <span aria-hidden>❓</span>
            <span>
              Entre <strong className="font-semibold text-slate-900">{left.label}</strong> e{" "}
              <strong className="font-semibold text-slate-900">{right.label}</strong>, qual dimensão
              deve ter maior prioridade na seleção do provedor?
              <span className="ml-1 text-red-500">*</span>
            </span>
          </legend>

          <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-3">
            {options.map((opt) => {
              const selected = value.preference === opt.id;
              return (
                <button
                  key={opt.id}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => onChange(selectPreference(value, opt.id))}
                  className={
                    "relative flex flex-col items-center gap-1.5 rounded-xl border px-3 py-4 text-center transition-all " +
                    (selected
                      ? "border-blue-500 bg-blue-50/70 ring-4 ring-blue-500/10"
                      : "border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50")
                  }
                >
                  {selected && (
                    <span
                      className="absolute right-2 top-2 flex h-5 w-5 items-center justify-center rounded-full bg-blue-600 text-[11px] font-bold text-white"
                      aria-hidden
                    >
                      ✓
                    </span>
                  )}
                  <span className="text-2xl leading-none" aria-hidden>
                    {opt.icon}
                  </span>
                  <span
                    className={
                      "text-sm font-medium " + (selected ? "text-blue-700" : "text-slate-700")
                    }
                  >
                    {opt.label}
                  </span>
                </button>
              );
            })}
          </div>
        </fieldset>

        {/* Etapa 2 — intensidade. Só existe quando há uma dimensão preferida:
            "igual importância" já fixa a razão em 1, e perguntar o quanto seria
            pedir uma resposta que não tem significado no AHP. */}
        {needsIntensity && (
          <fieldset className="mt-4 border-t border-slate-100 pt-4">
            <legend className="mb-3 flex gap-2 text-sm text-slate-700">
              <span aria-hidden>🎚</span>
              <span>
                Com que intensidade{" "}
                <strong className="font-semibold text-slate-900">{preferred?.label}</strong> deve
                pesar mais que <strong className="font-semibold text-slate-900">{other?.label}</strong>?
                <span className="ml-1 text-red-500">*</span>
              </span>
            </legend>

            <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
              {INTENSITIES.map((opt) => {
                const selected = value.intensity === opt.id;
                return (
                  <button
                    key={opt.id}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => onChange(selectIntensity(value, opt.id as PairwiseIntensity))}
                    className={
                      "rounded-xl border px-3 py-2.5 text-sm font-medium transition-all " +
                      (selected
                        ? "border-blue-500 bg-blue-50/70 text-blue-700 ring-4 ring-blue-500/10"
                        : "border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50")
                    }
                  >
                    {opt.label}
                  </button>
                );
              })}
            </div>
          </fieldset>
        )}

        {invalid && (
          <p className="mt-3 flex items-center gap-1.5 text-xs font-medium text-red-600">
            <span aria-hidden>⚠</span>
            {needsIntensity
              ? "Escolha a intensidade para concluir esta comparação."
              : "Escolha a dimensão prioritária ou marque igual importância."}
          </p>
        )}

        {/* Leitura interna do julgamento: útil para conferir a conversão durante o
            desenvolvimento, fora da build de produção — o gestor não precisa
            conhecer a escala numérica de Saaty para responder. Os mesmos valores
            aparecem na memória de cálculo do relatório, aí sim para todos. */}
        {import.meta.env.DEV && complete && (
          <p className="mt-3 rounded-lg bg-slate-50 px-3 py-2 font-mono text-[11px] text-slate-500">
            {value.preference === EQUAL
              ? `dev · preference=equal · intensity=null · saaty=1 · a(${left.id},${right.id})=1`
              : `dev · preference=${value.preference} · intensity=${value.intensity} · saaty=${
                  INTENSITIES.find((i) => i.id === value.intensity)?.saaty
                } · a(${left.id},${right.id})=${
                  value.preference === left.id
                    ? INTENSITIES.find((i) => i.id === value.intensity)?.saaty
                    : `1/${INTENSITIES.find((i) => i.id === value.intensity)?.saaty}`
                }`}
          </p>
        )}
      </div>
    </div>
  );
}
