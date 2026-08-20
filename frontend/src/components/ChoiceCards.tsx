/**
 * Seleção de uma alternativa em cartões, no lugar do `<select>`.
 *
 * As perguntas dos blocos A, B e C usam sempre a mesma escala ordenada de
 * relevância — cinco alternativas que iam para dentro de um dropdown, onde o
 * gestor precisava abrir, ler e comparar as opções uma a uma. Em cartões as
 * cinco ficam visíveis ao mesmo tempo e a resposta sai em um clique, do mesmo
 * jeito que já acontece nas comparações par-a-par do bloco D.
 *
 * O conjunto de opções vem do questions.json, então o componente não conhece a
 * escala: assume apenas que ela está **em ordem decrescente de intensidade**,
 * como as option_sets são declaradas, e desenha o medidor a partir da posição.
 */

interface Props {
  /** Nome do grupo de rádios — um por pergunta, senão a seleção vaza entre elas. */
  name: string;
  options: string[];
  value: string;
  onChange: (next: string) => void;
  invalid?: boolean;
}

/**
 * "Decisivo (critério indispensável)" vira título + apoio: o parêntese explica a
 * alternativa e, em corpo menor, para de competir com o rótulo na leitura rápida.
 */
function splitLabel(option: string): { title: string; hint?: string } {
  const m = option.match(/^(.*?)\s*\(([^)]+)\)\s*$/);
  return m ? { title: m[1], hint: m[2] } : { title: option };
}

/** Medidor de intensidade: a primeira alternativa acende todas as barras, a última só uma. */
function LevelMeter({ level, total, selected }: { level: number; total: number; selected: boolean }) {
  return (
    <span className="flex items-end gap-0.5" aria-hidden>
      {Array.from({ length: total }, (_, i) => (
        <span
          key={i}
          style={{ height: `${8 + i * 3}px` }}
          className={
            "w-1 rounded-full transition-colors " +
            (i < level
              ? selected
                ? "bg-blue-600"
                : "bg-slate-400"
              : selected
                ? "bg-blue-200"
                : "bg-slate-200")
          }
        />
      ))}
    </span>
  );
}

export default function ChoiceCards({ name, options, value, onChange, invalid }: Props) {
  return (
    <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-5">
      {options.map((opt, i) => {
        const selected = value === opt;
        const { title, hint } = splitLabel(opt);
        return (
          <label key={opt} className="cursor-pointer">
            {/* Rádio nativo: dá navegação por setas, seleção por teclado e
                semântica de grupo sem reimplementar nada disso à mão. */}
            <input
              type="radio"
              name={name}
              value={opt}
              checked={selected}
              onChange={() => onChange(opt)}
              aria-invalid={invalid}
              className="peer sr-only"
            />
            <span
              className={
                "relative flex h-full flex-row items-center gap-3 rounded-xl border px-3 py-3 transition-all " +
                "peer-focus-visible:ring-4 peer-focus-visible:ring-blue-500/25 " +
                "sm:flex-col sm:justify-start sm:gap-2 sm:px-3 sm:py-4 sm:text-center " +
                (selected
                  ? "border-blue-500 bg-blue-50/70 ring-4 ring-blue-500/10"
                  : invalid
                    ? "border-red-200 bg-white hover:border-red-300 hover:bg-red-50/40"
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
              <LevelMeter level={options.length - i} total={options.length} selected={selected} />
              <span className="flex flex-col sm:items-center">
                <span
                  className={
                    "text-sm font-medium leading-snug " +
                    (selected ? "text-blue-700" : "text-slate-700")
                  }
                >
                  {title}
                </span>
                {hint && (
                  <span
                    className={
                      "text-[11px] leading-snug " +
                      (selected ? "text-blue-600/80" : "text-slate-400")
                    }
                  >
                    {hint}
                  </span>
                )}
              </span>
            </span>
          </label>
        );
      })}
    </div>
  );
}
