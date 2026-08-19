import { useState } from "react";
import { Link } from "react-router-dom";
import type { AhpResult } from "../types";

const LABELS: Record<string, string> = {
  sustainability: "Sustentabilidade",
  performance: "Desempenho",
  security: "Segurança",
};

const fmt = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(2));

/** Razão de Saaty legível: 5 continua "5", 0,2 vira "1/5". */
function fmtRatio(r: number): string {
  if (r >= 1) return fmt(r);
  const inv = 1 / r;
  const near = Math.round(inv);
  return `1/${Math.abs(inv - near) < 0.05 ? near : inv.toFixed(2)}`;
}

/** "sustainability|performance" → "Sustentabilidade × Desempenho" */
function pairLabel(key: string): string {
  return key
    .split("|")
    .map((c) => LABELS[c] || c)
    .join(" × ");
}

/** Mostra a memória de cálculo do AHP: intensidades, matriz par a par e consistência. */
export default function AhpAudit({
  ahp,
  weights,
  reviewHref,
}: {
  ahp: AhpResult;
  weights: Record<string, number>;
  /**
   * Destino do botão "Revisar comparações". Só existe onde rever faz sentido —
   * na tela do próprio gestor. A área de gestão reexibe envios de terceiros e
   * omite o botão.
   */
  reviewHref?: string;
}) {
  const [open, setOpen] = useState(false);
  const ok = ahp.is_consistent;

  return (
    <div
      className={
        "rounded-2xl border bg-white shadow-sm " +
        (ok ? "border-slate-200" : "border-amber-300 ring-4 ring-amber-500/10")
      }
    >
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
            {ok ? "Julgamentos consistentes." : "As comparações apresentam inconsistência."}{" "}
            <span className="font-medium text-slate-500">
              RC ={" "}
              <span className={ok ? "text-emerald-600" : "text-amber-600"}>
                {ahp.consistency_ratio.toFixed(3)}
              </span>
            </span>
          </h3>
          <p className="mt-0.5 text-sm text-slate-500">
            {ok
              ? `Abaixo do limite de ${ahp.consistency_threshold} definido por Saaty — as suas comparações par-a-par são coerentes entre si.`
              : `Acima do limite de ${ahp.consistency_threshold} definido por Saaty: as suas comparações se contradizem (ex.: A > B e B > C, mas C > A). Revise suas prioridades antes de continuar com a recomendação final — enquanto o RC estiver acima do limite, o ranking abaixo é um resultado preliminar.`}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {!ok && reviewHref && (
            <Link
              to={reviewHref}
              className="rounded-lg bg-amber-600 px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-amber-700"
            >
              Revisar comparações
            </Link>
          )}
          <button
            type="button"
            onClick={() => setOpen(!open)}
            aria-expanded={open}
            className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
          >
            {open ? "Ocultar" : "Ver"} memória de cálculo
          </button>
        </div>
      </div>

      {open && (
        <div className="space-y-5 border-t border-slate-100 bg-slate-50/60 p-5">
          <div>
            <h4 className="mb-2 text-sm font-semibold text-slate-800">
              1. Julgamentos par a par informados (perguntas 17–19)
            </h4>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[34rem] text-sm">
                <thead className="text-xs uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="py-1.5 text-left font-semibold">Par comparado</th>
                    <th className="py-1.5 text-left font-semibold">Resposta</th>
                    <th className="py-1.5 text-right font-semibold">Saaty</th>
                    <th className="py-1.5 text-right font-semibold">
                      a<sub>ij</sub>
                    </th>
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {Object.entries(ahp.judgments).map(([key, j]) => (
                    <tr key={key} className="border-t border-slate-200">
                      <td className="py-1.5 pr-3 text-slate-700">{pairLabel(key)}</td>
                      <td className="py-1.5 pr-3 text-slate-500">{j.choice}</td>
                      <td className="py-1.5 pr-3 text-right text-slate-500">
                        {/* Intensidade antes da direção; envios antigos não a registram */}
                        {j.saaty_intensity != null ? fmt(j.saaty_intensity) : "—"}
                      </td>
                      <td className="py-1.5 text-right font-semibold text-slate-900">
                        {fmtRatio(j.ratio)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {ahp.missing_judgments.length > 0 && (
              <p className="mt-1.5 text-xs text-amber-600">
                Sem julgamento para {ahp.missing_judgments.map(pairLabel).join(", ")} — a célula
                ficou em 1 (indiferença), o que não é uma preferência declarada pelo gestor.
              </p>
            )}
          </div>

          <div>
            <h4 className="mb-2 text-sm font-semibold text-slate-800">
              Relevância dos indicadores por dimensão (perguntas 1–15, escala 1–5)
            </h4>
            <div className="flex flex-wrap gap-3 text-sm tabular-nums">
              {ahp.criteria_order.map((c) => (
                <span key={c} className="rounded-lg bg-white px-3 py-1.5 ring-1 ring-slate-200">
                  {LABELS[c] || c}:{" "}
                  <strong className="text-slate-900">
                    {fmt(ahp.relevance_by_criterion[c])}
                  </strong>
                </span>
              ))}
            </div>
            <p className="mt-1.5 text-xs text-slate-400">
              Fora do cálculo dos pesos: a escala 1–5 mede relevância individual de cada indicador,
              enquanto a escala de Saaty mede preferência relativa entre duas dimensões. Estes
              valores contextualizam o relatório e a justificativa, não a matriz.
            </p>
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
            <strong>Nota metodológica:</strong> a matriz vem da elicitação par a par direta — em
            cada comparação (perguntas 17–19) o gestor informa a dimensão prioritária e a
            intensidade verbal, convertidas no servidor para a escala de Saaty (moderadamente = 3,
            fortemente = 5, muito fortemente = 7, extremamente = 9, igual = 1) e para o seu
            recíproco quando a preferência é pela dimensão à direita. Os três julgamentos preenchem
            a matriz por reciprocidade, e a razão de consistência mede a coerência dos próprios
            julgamentos. Nenhum peso é definido pela IA: ela apenas redige a justificativa a partir
            das respostas dissertativas.
          </p>
        </div>
      )}
    </div>
  );
}
