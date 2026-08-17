import { useState } from "react";
import type { SynthesisResult } from "../types";

const LABELS: Record<string, string> = {
  sustainability: "Sustentabilidade",
  performance: "Desempenho",
  security: "Segurança",
};

const f3 = (n: number) => n.toFixed(3);
const f4 = (n: number) => n.toFixed(4);
const pct = (n: number) => `${(n * 100).toFixed(1)}%`;

/**
 * Memória de cálculo do score final (síntese das alternativas, modo distributivo).
 *
 * Mostra a aritmética inteira célula a célula — nota bruta → normalizada dentro do
 * critério → multiplicada pelo peso → somada — de modo que qualquer linha da
 * tabela de ranking possa ser refeita à mão a partir do relatório.
 */
export default function SynthesisAudit({ synthesis }: { synthesis: SynthesisResult }) {
  const [open, setOpen] = useState(false);
  const { criteria_order: criteria, weights, column_totals: totals, providers } = synthesis;
  const top = providers[0];

  return (
    <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center gap-3 p-5">
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-xl"
          aria-hidden
        >
          🧮
        </div>
        <div className="min-w-[16rem] flex-1">
          <h3 className="font-bold text-slate-900">Como o score final foi calculado</h3>
          <p className="mt-0.5 text-sm text-slate-500">
            score = Σ (peso do critério × nota normalizada). As prioridades somam 1 entre os
            provedores, por isso ficam próximas de 1/{providers.length}.
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
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <h4 className="mb-2 text-sm font-semibold text-slate-800">As duas etapas</h4>
            <ol className="space-y-2 text-sm leading-relaxed text-slate-600">
              <li>
                <strong className="text-slate-800">1. Normalização por critério.</strong> A nota do
                provedor é dividida pela soma das notas de todos os provedores naquele critério,
                para que as notas virem proporções comparáveis entre si (somam 1 por critério).
              </li>
              <li>
                <strong className="text-slate-800">2. Agregação ponderada.</strong> Cada proporção é
                multiplicada pelo peso do critério — o peso vem do autovetor da matriz par a par do
                seu questionário — e as três parcelas são somadas.
              </li>
            </ol>
          </div>

          <div>
            <h4 className="mb-2 text-sm font-semibold text-slate-800">
              Conta completa, provedor por provedor
            </h4>
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="w-full min-w-[46rem] text-sm">
                <thead className="bg-slate-50 text-slate-500">
                  <tr>
                    <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide">
                      Provedor
                    </th>
                    {criteria.map((c) => (
                      <th key={c} className="px-3 py-2 text-left font-semibold">
                        <span className="block text-xs uppercase tracking-wide">
                          {LABELS[c] || c}
                        </span>
                        <span className="block text-[11px] font-normal text-slate-400">
                          peso {pct(weights[c])} · Σ notas {f3(totals[c])}
                        </span>
                      </th>
                    ))}
                    <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide">
                      Score final
                    </th>
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {providers.map((p) => (
                    <tr key={p.id} className="border-t border-slate-100 align-top">
                      <td className="px-3 py-2.5 font-medium text-slate-800">{p.name}</td>
                      {criteria.map((c) => {
                        const cell = p.cells[c];
                        return (
                          <td key={c} className="px-3 py-2.5">
                            <span className="block text-[11px] text-slate-400">
                              {f3(cell.raw)} ÷ {f3(totals[c])} = {f4(cell.normalized)}
                            </span>
                            <span className="block text-slate-700">
                              × {f4(cell.weight)} ={" "}
                              <strong className="text-slate-900">{f4(cell.contribution)}</strong>
                            </span>
                          </td>
                        );
                      })}
                      <td className="px-3 py-2.5 text-right font-bold text-slate-900">
                        {f3(p.score)}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr className="border-t border-slate-200 bg-slate-50 text-xs text-slate-500">
                    <td className="px-3 py-2" colSpan={criteria.length + 1}>
                      Verificação: as prioridades do modo distributivo somam 1
                    </td>
                    <td className="px-3 py-2 text-right font-semibold tabular-nums text-slate-700">
                      {f3(synthesis.score_total)}
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>

          {top && (
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <h4 className="mb-2 text-sm font-semibold text-slate-800">
                Refazendo a conta de {top.name}
              </h4>
              <p className="font-mono text-xs leading-relaxed text-slate-600">
                {criteria
                  .map((c) => `${f4(top.cells[c].weight)} × ${f4(top.cells[c].normalized)}`)
                  .join("  +  ")}{" "}
                = <strong className="text-slate-900">{f3(top.score)}</strong>
              </p>
            </div>
          )}

          <p className="border-t border-slate-200 pt-3 text-xs leading-relaxed text-slate-500">
            <strong>De onde vem cada número:</strong> os <em>pesos</em> vêm exclusivamente das suas
            comparações par-a-par (perguntas 17–19), pelo autovetor da matriz de Saaty. As{" "}
            <em>notas</em> dos provedores vêm da base de referência do sistema — não das suas
            respostas nem dos documentos indexados, que alimentam apenas a seção de evidências.
            Trocando os pesos, muda o score; trocando as notas, muda a ordem.
          </p>
        </div>
      )}
    </div>
  );
}
