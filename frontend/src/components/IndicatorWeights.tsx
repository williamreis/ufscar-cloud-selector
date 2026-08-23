import { useState } from "react";
import type { IndicatorWeights as IndicatorWeightsData } from "../types";

const DIMENSION_LABELS: Record<string, string> = {
  sustainability: "Sustentabilidade",
  performance: "Desempenho",
  security: "Segurança",
};

const RELEVANCE_STATE: Record<string, string> = {
  answered: "respondida",
  unknown: "não sei",
  missing: "sem resposta",
};

const f4 = (n: number) => n.toFixed(4);
const pct = (n: number) => `${(n * 100).toFixed(1)}%`;

/**
 * Os três níveis de peso de cada indicador (§4.4.1.4, Equações 3 e 4).
 *
 * A dissertação afirma duas vezes que a recomendação vem acompanhada deles — na
 * §4.4.1.5 ("acompanhada dos pesos das dimensões, dos pesos locais e globais dos
 * indicadores") e na §5.5, ao descrever o que a auditoria permite consultar.
 *
 * O que a tabela responde é a pergunta que o gestor faz depois de ver o ranking:
 * *por que este indicador pesou o que pesou?* A resposta é a linha inteira —
 * a alternativa que ele marcou, o coeficiente que ela virou, o peso local dentro
 * da dimensão, o peso da dimensão vindo do AHP, e o produto dos dois.
 *
 * O peso efetivo é uma quarta coluna e não faz parte das Equações 3 e 4: ele é o
 * peso global renormalizado sobre os indicadores que sobraram (§11.2), e difere
 * do global sempre que algum indicador saiu por falta de evidência. Mostrar os
 * dois lado a lado é o que torna essa diferença visível em vez de misteriosa.
 */
export default function IndicatorWeights({
  weights,
  criteriaWeights,
}: {
  weights: IndicatorWeightsData;
  criteriaWeights: Record<string, number>;
}) {
  const [open, setOpen] = useState(false);
  const { indicators, dimensions_needing_review: needingReview } = weights;

  const byDimension = Object.keys(criteriaWeights).map((dimension) => ({
    dimension,
    rows: indicators.filter((i) => i.dimension === dimension),
  }));

  const semPeso = indicators.filter((i) => i.global_weight === null).length;
  const foraDaComparacao = indicators.filter(
    (i) => i.global_weight !== null && !i.is_valid_for_comparison,
  ).length;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center gap-3 p-5">
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-xl"
          aria-hidden
        >
          ⚖️
        </div>
        <div className="min-w-[16rem] flex-1">
          <h3 className="font-bold text-slate-900">
            Peso de cada indicador, e de onde ele veio
          </h3>
          <p className="mt-0.5 text-sm text-slate-500">
            peso local = coeficiente ÷ soma da dimensão · peso global = peso da dimensão ×
            peso local
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
        >
          {open ? "Ocultar" : "Ver"} pesos por indicador
        </button>
      </div>

      {open && (
        <div className="space-y-5 border-t border-slate-100 bg-slate-50/60 p-5">
          {needingReview.length > 0 && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-relaxed text-amber-900">
              <strong>
                {needingReview.map((d) => DIMENSION_LABELS[d] || d).join(", ")}
              </strong>{" "}
              não teve nenhum indicador com relevância informada. Os pesos locais dessa
              dimensão não puderam ser calculados — o modelo não distribui pesos iguais
              para tapar a lacuna, porque isso fabricaria um julgamento que você não deu.
            </div>
          )}

          {byDimension.map(({ dimension, rows }) => (
            <div key={dimension}>
              <h4 className="mb-2 flex flex-wrap items-baseline gap-2 text-sm font-semibold text-slate-800">
                {DIMENSION_LABELS[dimension] || dimension}
                <span className="font-normal text-slate-500">
                  peso da dimensão {pct(criteriaWeights[dimension] ?? 0)} (AHP)
                </span>
              </h4>
              <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
                <table className="w-full min-w-[46rem] text-sm">
                  <thead className="bg-slate-50 text-slate-500">
                    <tr>
                      {[
                        "Indicador",
                        "Sua resposta",
                        "Coeficiente",
                        "Peso local",
                        "Peso global",
                        "Peso efetivo",
                      ].map((h) => (
                        <th
                          key={h}
                          className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide"
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="tabular-nums">
                    {rows.map((row) => (
                      <tr
                        key={row.indicator_id}
                        className={`border-t border-slate-100 align-top ${
                          row.is_valid_for_comparison ? "" : "bg-slate-50/70"
                        }`}
                      >
                        <td className="px-3 py-2.5">
                          <span className="block font-medium text-slate-800">{row.name}</span>
                          {!row.is_valid_for_comparison && row.global_weight !== null && (
                            <span className="block text-[11px] text-amber-700">
                              fora da comparação: {row.excluded_reason ?? "sem evidência"}
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2.5 text-slate-600">
                          {row.relevance_state === "answered" ? (
                            <span className="text-[12px]">
                              {RELEVANCE_STATE[row.relevance_state]}
                            </span>
                          ) : (
                            <span className="text-[12px] text-slate-400">
                              {RELEVANCE_STATE[row.relevance_state] ?? row.relevance_state}
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2.5 text-slate-700">
                          {row.relevance_coefficient ?? "—"}
                        </td>
                        <td className="px-3 py-2.5 text-slate-700">
                          {row.local_weight === null ? "—" : f4(row.local_weight)}
                        </td>
                        <td className="px-3 py-2.5 font-semibold text-slate-900">
                          {row.global_weight === null ? "—" : f4(row.global_weight)}
                        </td>
                        <td className="px-3 py-2.5 text-slate-700">
                          {row.effective_weight === null ? "—" : f4(row.effective_weight)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="border-t border-slate-200 bg-slate-50 text-xs text-slate-500">
                      <td className="px-3 py-2" colSpan={3}>
                        Soma dos pesos locais da dimensão (deve ser 1)
                      </td>
                      <td className="px-3 py-2 font-semibold tabular-nums text-slate-700">
                        {f4(
                          rows.reduce((soma, r) => soma + (r.local_weight ?? 0), 0),
                        )}
                      </td>
                      <td className="px-3 py-2" colSpan={2}></td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </div>
          ))}

          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-600">
              <strong className="block text-slate-800">
                Soma dos pesos globais: {f4(weights.global_weight_sum)}
              </strong>
              Fecha em 1 quando todas as dimensões têm pelo menos um indicador com
              relevância informada.
              {semPeso > 0 && (
                <>
                  {" "}
                  {semPeso} indicador{semPeso === 1 ? "" : "es"} ficou sem peso por falta
                  de resposta — nunca com zero, que afirmaria irrelevância.
                </>
              )}
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-600">
              <strong className="block text-slate-800">
                Soma dos pesos efetivos: {f4(weights.effective_weight_sum)}
              </strong>
              Os pesos globais dos indicadores que reuniram evidência comparável,
              renormalizados para somar 1.
              {foraDaComparacao > 0 && (
                <>
                  {" "}
                  {foraDaComparacao} indicador{foraDaComparacao === 1 ? "" : "es"} com peso
                  ficou fora da comparação e teve o seu peso redistribuído.
                </>
              )}
            </div>
          </div>

          <p className="border-t border-slate-200 pt-3 text-xs leading-relaxed text-slate-500">
            <strong>Como ler:</strong> o <em>coeficiente</em> traduz a alternativa que você
            marcou nas perguntas 1–15. O <em>peso local</em> é esse coeficiente dividido
            pela soma dos coeficientes da mesma dimensão — por isso cada dimensão fecha em
            1. O <em>peso global</em> multiplica o peso local pelo peso da dimensão, que
            veio das suas comparações par-a-par. O <em>peso efetivo</em> é o global
            renormalizado sobre os indicadores que entraram na comparação, e é ele que
            multiplicou o desempenho na pontuação final.
          </p>
        </div>
      )}
    </div>
  );
}
