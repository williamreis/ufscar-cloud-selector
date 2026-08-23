import { useState } from "react";
import type { SynthesisIndicator, SynthesisResult } from "../types";

const LABELS: Record<string, string> = {
  sustainability: "Sustentabilidade",
  performance: "Desempenho",
  security: "Segurança",
};

const EXCLUSION_REASONS: Record<string, string> = {
  no_evidence: "nenhum provedor tem evidência",
  missing_for_some_providers: "falta evidência em algum provedor",
  non_discriminative: "todos os valores iguais a zero",
  invalid_for_comparison: "valores incompatíveis com a fórmula",
  no_weight: "sem peso (relevância não informada)",
};

const f3 = (n: number) => n.toFixed(3);
const f4 = (n: number) => n.toFixed(4);
const pct = (n: number) => `${(n * 100).toFixed(1)}%`;

/** Número que entrou na conta, com a unidade quando há. */
function computedValue(row: SynthesisIndicator): string {
  if (row.original_value === null) return "—";
  const n = row.original_value;
  return row.unit ? `${n} ${row.unit}` : String(n);
}

/**
 * Nível de atendimento em forma legível.
 *
 * Os níveis vêm da configuração (o Quadro 23 vive no `scales.json`), então aqui
 * só se ajusta a apresentação: "nao_identificado" → "Nao identificado" seria
 * pior que o original, e por isso o mapa acentua os nomes conhecidos e deixa
 * qualquer outro passar como veio.
 */
const LEVEL_LABELS: Record<string, string> = {
  nao_identificado: "Não identificado",
  nao_atendido: "Não atendido",
  baixo: "Baixo",
  moderado: "Moderado",
  alto: "Alto",
  completo: "Completo",
  comprovado: "Comprovado",
};

function levelLabel(category: string): string {
  return LEVEL_LABELS[category] ?? category;
}

/**
 * Memória de cálculo do score final (Equação 5).
 *
 * Mostra a cadeia inteira de cada indicador — valor publicado no documento →
 * normalizado pela Equação 1 ou 2 → multiplicado pelo peso efetivo → somado —
 * de modo que qualquer linha do ranking possa ser refeita à mão a partir do
 * relatório e conferida contra o PDF de origem.
 *
 * Os indicadores que ficaram fora da conta aparecem com o motivo, e não com
 * zero: a §11 é explícita em que ausência de evidência não é desempenho nulo.
 */
export default function SynthesisAudit({ synthesis }: { synthesis: SynthesisResult }) {
  const [open, setOpen] = useState(false);
  const {
    criteria_order: criteria,
    dimension_weights: dimWeights,
    providers,
    valid_indicators: valid,
    excluded_indicators: excluded,
  } = synthesis;
  const top = providers[0];
  const excludedList = Object.entries(excluded);

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
            score = Σ (peso do indicador × desempenho normalizado), sobre os{" "}
            {valid.length} indicador{valid.length === 1 ? "" : "es"} com evidência comparável em
            todos os provedores.
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
            <h4 className="mb-2 text-sm font-semibold text-slate-800">As quatro etapas</h4>
            <ol className="space-y-2 text-sm leading-relaxed text-slate-600">
              <li>
                <strong className="text-slate-800">1. Evidência.</strong> Cada indicador tem uma
                consulta própria à base documental. O trecho recuperado é interpretado e o valor
                publicado — ou a categoria da rubrica — é extraído com a fonte anexada.
              </li>
              <li>
                <strong className="text-slate-800">2. Normalização.</strong> Para indicadores em
                que maior é melhor, valor ÷ maior valor entre os provedores. Para os de
                minimização (latência, PUE), menor valor ÷ valor. Os dois deixam o resultado
                entre 0 e 1.
              </li>
              <li>
                <strong className="text-slate-800">3. Peso efetivo.</strong> Peso da dimensão (do
                AHP) × peso local do indicador (da sua resposta de relevância), renormalizado
                sobre os indicadores que sobraram — a soma volta a 1.
              </li>
              <li>
                <strong className="text-slate-800">4. Agregação.</strong> Cada desempenho
                normalizado é multiplicado pelo seu peso efetivo, e as parcelas são somadas.
              </li>
            </ol>
          </div>

          {excludedList.length > 0 && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
              <h4 className="mb-2 text-sm font-semibold text-amber-900">
                Fora da conta ({excludedList.length})
              </h4>
              <p className="mb-2 text-sm leading-relaxed text-amber-900">
                Estes indicadores não entraram no cálculo. Nenhum provedor foi penalizado por
                isso: o indicador sai para <em>todos</em>, e os pesos dos que ficaram são
                redistribuídos.
              </p>
              <ul className="space-y-1 text-sm text-amber-900">
                {excludedList.map(([id, reason]) => (
                  <li key={id}>
                    <code className="rounded bg-amber-100 px-1 text-[12px]">{id}</code> —{" "}
                    {EXCLUSION_REASONS[reason] || reason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <h4 className="mb-2 text-sm font-semibold text-slate-800">
              Contribuição por dimensão
            </h4>
            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
              <table className="w-full min-w-[40rem] text-sm">
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
                          peso {pct(dimWeights[c] ?? 0)}
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
                        if (!cell) return <td key={c} className="px-3 py-2.5 text-slate-400">—</td>;
                        return (
                          <td key={c} className="px-3 py-2.5">
                            <span className="block text-[11px] text-slate-400">
                              {cell.performance === undefined
                                ? "sem evidência comparável"
                                : `desempenho ${f3(cell.performance)}`}
                            </span>
                            <span className="block text-slate-700">
                              contribui{" "}
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
                    <td className="px-3 py-2" colSpan={criteria.length + 2}>
                      Verificação: os pesos dos indicadores válidos somam{" "}
                      <strong className="tabular-nums text-slate-700">
                        {f3(synthesis.effective_weight_sum)}
                      </strong>
                      {synthesis.has_ties && " · há empate no ranking"}
                    </td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </div>

          {top && (
            <div>
              <h4 className="mb-2 text-sm font-semibold text-slate-800">
                Indicador a indicador — {top.name}
              </h4>
              <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
                <table className="w-full min-w-[52rem] text-sm">
                  <thead className="bg-slate-50 text-slate-500">
                    <tr>
                      {[
                        "Indicador",
                        "Evidência",
                        "Extraído do documento",
                        "Normalizado",
                        "Peso",
                        "Contribuição",
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
                    {top.indicators.map((row) => (
                      <tr
                        key={row.indicator_id}
                        className={`border-t border-slate-100 align-top ${
                          row.in_comparison ? "" : "bg-slate-50/70 text-slate-400"
                        }`}
                      >
                        <td className="px-3 py-2.5">
                          <span className="block font-medium text-slate-800">{row.name}</span>
                          <span className="block text-[11px] text-slate-400">
                            {LABELS[row.dimension] || row.dimension} ·{" "}
                            {row.direction === "minimize" ? "menor é melhor" : "maior é melhor"}
                          </span>
                        </td>
                        <td className="max-w-[18rem] px-3 py-2.5">
                          <span className="block text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                            {row.status}
                          </span>
                          <span className="block text-[12px] leading-snug text-slate-600">
                            {row.rejection || row.summary || "—"}
                          </span>
                          {row.source_document && (
                            <span className="block text-[11px] text-slate-400">
                              fonte: {row.source_document}
                            </span>
                          )}
                        </td>
                        <td className="px-3 py-2.5">
                          <span className="block text-slate-700">
                            {row.extracted_value || "—"}
                          </span>
                          <span
                            className="block text-[11px] text-slate-400"
                            title={row.category_condition ?? undefined}
                          >
                            {row.category
                              ? `${levelLabel(row.category)} → ${computedValue(row)}`
                              : computedValue(row)}
                          </span>
                        </td>
                        <td className="px-3 py-2.5">
                          {row.normalized_value === null ? "—" : f4(row.normalized_value)}
                        </td>
                        <td className="px-3 py-2.5">
                          {row.effective_weight === null ? "—" : f4(row.effective_weight)}
                        </td>
                        <td className="px-3 py-2.5 font-semibold text-slate-900">
                          {row.contribution === null ? "—" : f4(row.contribution)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="border-t border-slate-200 bg-slate-50 text-xs text-slate-500">
                      <td className="px-3 py-2" colSpan={5}>
                        Soma das contribuições = score final de {top.name}
                      </td>
                      <td className="px-3 py-2 font-semibold tabular-nums text-slate-700">
                        {f3(top.score)}
                      </td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </div>
          )}

          <p className="border-t border-slate-200 pt-3 text-xs leading-relaxed text-slate-500">
            <strong>De onde vem cada número:</strong> os <em>pesos das dimensões</em> vêm das suas
            comparações par-a-par (perguntas 17–19), pelo método AHP. Os <em>pesos locais</em> vêm
            das suas respostas de relevância (perguntas 1–15). Os <em>valores publicados</em> vêm
            dos documentos indexados, extraídos indicador a indicador com a fonte anexada. A
            normalização e a soma são determinísticas — o modelo de linguagem lê o documento, mas
            não atribui nota, peso nem posição.
          </p>
        </div>
      )}
    </div>
  );
}
