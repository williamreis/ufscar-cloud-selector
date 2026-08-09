import { useState } from "react";
import type { AhpResult } from "../types";

const LABELS: Record<string, string> = {
  sustainability: "Sustentabilidade",
  performance: "Desempenho",
  security: "Segurança",
};

const fmt = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(2));

/** Mostra a memória de cálculo do AHP: intensidades, matriz par a par e consistência. */
export default function AhpAudit({ ahp, weights }: { ahp: AhpResult; weights: Record<string, number> }) {
  const [open, setOpen] = useState(false);
  const ok = ahp.is_consistent;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center gap-3 p-5">
        <div
          className={
            "flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-xl " +
            (ok ? "bg-emerald-50" : "bg-amber-50")
          }
          aria-hidden
        >
          {ok ? "✓" : "⚠"}
        </div>
        <div className="min-w-[16rem] flex-1">
          <h3 className="font-bold text-slate-900">
            Razão de consistência (CR):{" "}
            <span className={ok ? "text-emerald-600" : "text-amber-600"}>
              {ahp.consistency_ratio.toFixed(3)}
            </span>
          </h3>
          <p className="mt-0.5 text-sm text-slate-500">
            {ok
              ? `Abaixo do limite de ${ahp.consistency_threshold} definido por Saaty — os julgamentos derivados do questionário são consistentes.`
              : `Acima do limite de ${ahp.consistency_threshold} definido por Saaty — as respostas se contradizem entre si e os pesos devem ser interpretados com cautela.`}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
        >
          {open ? "Ocultar" : "Ver"} memória de cálculo
        </button>
      </div>

      {open && (
        <div className="space-y-5 border-t border-slate-100 bg-slate-50/60 p-5">
          <div>
            <h4 className="mb-2 text-sm font-semibold text-slate-800">
              1. Intensidade por critério (escala 1–5)
            </h4>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[34rem] text-sm">
                <thead className="text-xs uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="py-1.5 text-left font-semibold">Critério</th>
                    <th className="py-1.5 text-right font-semibold">Perguntas 1–8</th>
                    <th className="py-1.5 text-right font-semibold">Perguntas 10–14</th>
                    <th className="py-1.5 text-right font-semibold">Ajuste da IA</th>
                    <th className="py-1.5 text-right font-semibold">Final</th>
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {ahp.criteria_order.map((c) => (
                    <tr key={c} className="border-t border-slate-200">
                      <td className="py-1.5 text-slate-700">{LABELS[c] || c}</td>
                      <td className="py-1.5 text-right text-slate-500">{fmt(ahp.base_scores[c])}</td>
                      <td className="py-1.5 text-right text-slate-500">
                        {ahp.comparative_adjustments[c] > 0 ? "+" : ""}
                        {fmt(ahp.comparative_adjustments[c])}
                      </td>
                      <td className="py-1.5 text-right text-slate-500">
                        {ahp.llm_adjustments[c] > 0 ? "+" : ""}
                        {fmt(ahp.llm_adjustments[c])}
                      </td>
                      <td className="py-1.5 text-right font-semibold text-slate-900">
                        {fmt(ahp.intensities[c])}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div>
            <h4 className="mb-2 text-sm font-semibold text-slate-800">
              2. Matriz de comparação par a par (escala de Saaty)
            </h4>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[30rem] text-sm">
                <thead className="text-xs uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="py-1.5 text-left font-semibold"> </th>
                    {ahp.criteria_order.map((c) => (
                      <th key={c} className="py-1.5 text-right font-semibold">
                        {LABELS[c] || c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {ahp.pairwise_matrix.map((row, i) => (
                    <tr key={i} className="border-t border-slate-200">
                      <td className="py-1.5 text-slate-700">
                        {LABELS[ahp.criteria_order[i]] || ahp.criteria_order[i]}
                      </td>
                      {row.map((v, j) => (
                        <td
                          key={j}
                          className={
                            "py-1.5 text-right " +
                            (i === j ? "font-semibold text-slate-900" : "text-slate-500")
                          }
                        >
                          {v.toFixed(2)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-1.5 text-xs text-slate-400">
              Matriz recíproca (a<sub>ij</sub> = 1/a<sub>ji</sub>), diagonal unitária.
            </p>
          </div>

          <div>
            <h4 className="mb-2 text-sm font-semibold text-slate-800">
              3. Autovetor principal → pesos
            </h4>
            <div className="flex flex-wrap gap-3 text-sm tabular-nums">
              {ahp.criteria_order.map((c) => (
                <span key={c} className="rounded-lg bg-white px-3 py-1.5 ring-1 ring-slate-200">
                  {LABELS[c] || c}:{" "}
                  <strong className="text-slate-900">{(weights[c] * 100).toFixed(1)}%</strong>
                </span>
              ))}
            </div>
            <p className="mt-2 text-xs text-slate-500">
              λ<sub>max</sub> = {ahp.lambda_max.toFixed(4)} · IC ={" "}
              {ahp.consistency_index.toFixed(4)} · RC = IC / IR ={" "}
              {ahp.consistency_ratio.toFixed(4)}
            </p>
          </div>

          <p className="border-t border-slate-200 pt-3 text-xs leading-relaxed text-slate-500">
            <strong>Nota metodológica:</strong> no AHP clássico o gestor informa cada comparação
            diretamente ("quanto A é mais importante que B, de 1 a 9"). Aqui a matriz é{" "}
            <em>derivada</em> das respostas do questionário, que não pede as comparações uma a uma —
            uma adaptação determinística e auditável, mas que não substitui a elicitação par a par
            direta.
          </p>
        </div>
      )}
    </div>
  );
}
