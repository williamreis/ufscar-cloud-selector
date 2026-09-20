import { useState } from "react";
import type { SynthesisIndicator, SynthesisResult } from "../types";

const LABELS: Record<string, string> = {
  sustainability: "Sustentabilidade",
  performance: "Desempenho",
  security: "Segurança",
};

export const EXCLUSION_REASONS: Record<string, string> = {
  no_evidence: "nenhum provedor tem evidência",
  missing_for_some_providers: "falta evidência em algum provedor",
  non_discriminative: "mesma nota para todos — não altera a ordem",
  invalid_for_comparison: "valores incompatíveis com a fórmula",
  no_weight: "sem peso (relevância não informada)",
};

const f3 = (n: number) => n.toFixed(3);
const f4 = (n: number) => n.toFixed(4);
const pct = (n: number) => `${(n * 100).toFixed(1)}%`;

// Abaixo disso duas notas normalizadas são a mesma nota: evita apontar como
// "diferença" o resíduo de ponto flutuante de uma divisão.
const SAME_VALUE_EPS = 1e-9;
// Diferença entre o peso do AHP e o peso aplicado que vale a pena explicar —
// meio ponto percentual some no arredondamento de `pct`.
const WEIGHT_SHIFT_MIN = 0.005;

type SynthesisProvider = SynthesisResult["providers"][number];

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

/** Como o valor do indicador aparece para o gestor: nível da rubrica ou número. */
function shownValue(row: SynthesisIndicator): string {
  return row.category ? levelLabel(row.category) : computedValue(row);
}

/**
 * Peso que cada dimensão teve de fato na soma: a soma dos pesos efetivos dos
 * seus indicadores válidos.
 *
 * Difere do peso do AHP sempre que a exclusão de indicadores foi desigual entre
 * as dimensões, porque a renormalização da §11.2 redistribui o peso dos
 * excluídos entre **todos** os que ficaram. É com este peso — e não com o do
 * AHP — que `peso × nota da dimensão` fecha a pontuação final.
 */
export function appliedDimensionWeights(synthesis: SynthesisResult): Record<string, number> {
  // O backend passou a publicar esta soma (`dimension_effective_weights`), que
  // é a autoridade: é o número com que a dimensão entrou na Equação 5.
  //
  // A soma no cliente continua como alternativa porque a área de gestão
  // reexibe `response_json` gravado — e nenhum relatório anterior a esse campo
  // o tem. Recalcular ali dá o mesmo valor; cair para zero apagaria o peso de
  // todo relatório antigo.
  if (synthesis.dimension_effective_weights) {
    return synthesis.dimension_effective_weights;
  }
  const pesos: Record<string, number> = Object.fromEntries(
    synthesis.criteria_order.map((c) => [c, 0]),
  );
  for (const indicator of synthesis.indicators) {
    pesos[indicator.dimension] = (pesos[indicator.dimension] ?? 0) + indicator.effective_weight;
  }
  return pesos;
}

/** Indicador válido cuja nota normalizada não é a mesma em todos os provedores. */
interface DecisiveIndicator {
  id: string;
  name: string;
  weight: number;
  rows: (SynthesisIndicator | undefined)[];
  bestContribution: number;
}

function decisiveIndicators(synthesis: SynthesisResult): DecisiveIndicator[] {
  const resultado: DecisiveIndicator[] = [];
  for (const indicator of synthesis.indicators) {
    const rows = synthesis.providers.map((p) =>
      p.indicators.find((r) => r.indicator_id === indicator.indicator_id),
    );
    const notas = rows
      .map((r) => r?.normalized_value)
      .filter((v): v is number => typeof v === "number");
    if (notas.length < 2 || Math.max(...notas) - Math.min(...notas) <= SAME_VALUE_EPS) continue;
    resultado.push({
      id: indicator.indicator_id,
      name: indicator.name,
      weight: indicator.effective_weight,
      rows,
      bestContribution: Math.max(...rows.map((r) => r?.contribution ?? 0)),
    });
  }
  // O que mais pesa na diferença primeiro.
  return resultado.sort((a, b) => b.weight - a.weight);
}

/**
 * Explicação da pontuação final, em camadas.
 *
 * A camada visível reduz a conta a `Σ (peso da dimensão × nota na dimensão)`,
 * com uma tabela que o gestor refaz com uma calculadora, e aponta os poucos
 * indicadores que de fato separaram os provedores. A memória de cálculo
 * completa — cada indicador, do valor publicado no documento à contribuição —
 * continua disponível sob demanda, para que qualquer linha do ranking possa ser
 * conferida contra o PDF de origem.
 *
 * Nada aqui recalcula o ranking: tudo é lido ou somado a partir da síntese que o
 * backend devolveu, então um envio antigo reexibido na gestão explica a mesma
 * conta que o gerou. Os indicadores fora da conta aparecem com o motivo, e não
 * com zero: a §11 é explícita em que ausência de evidência não é desempenho nulo.
 */
export default function SynthesisAudit({
  synthesis,
  tieTolerance,
}: {
  synthesis: SynthesisResult;
  /** Margem de indiferença do ranking; sem ela a regra de empate não é exibida. */
  tieTolerance?: number;
}) {
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

  const applied = appliedDimensionWeights(synthesis);
  const decisive = decisiveIndicators(synthesis);
  const indicatorsTotal = (dimension: string) =>
    top?.indicators.filter((r) => r.dimension === dimension).length ?? 0;
  const indicatorsValid = (dimension: string) =>
    synthesis.indicators.filter((i) => i.dimension === dimension).length;
  // Indicadores que reuniram evidência mas deram a mesma nota a todos. Com a
  // regra do `scales.json` ativa eles saem da soma e passam a viver aqui, em
  // `excluded` — não são lacuna, são resultado.
  const equivalentes = excludedList.filter(([, motivo]) => motivo === "non_discriminative");
  const shiftedDimensions = criteria.filter(
    (c) => Math.abs((applied[c] ?? 0) - (dimWeights[c] ?? 0)) >= WEIGHT_SHIFT_MIN,
  );

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
          <h3 className="font-bold text-slate-900">Como a pontuação final foi calculada</h3>
          <p className="mt-0.5 text-sm text-slate-500">
            Cada provedor recebe uma nota de 0 a 1 em cada dimensão. A pontuação final é a soma
            dessas notas, cada uma multiplicada pelo peso da dimensão.
          </p>
        </div>
      </div>

      <div className="space-y-5 border-t border-slate-100 p-5">
        {/* Camada 1 — a conta inteira em uma tabela */}
        <div>
          <h4 className="mb-2 text-sm font-semibold text-slate-800">
            1. Peso × nota em cada dimensão
          </h4>
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full min-w-[36rem] text-sm">
              <thead className="bg-slate-50 text-slate-500">
                <tr>
                  <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide">
                    Dimensão
                  </th>
                  <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide">
                    Peso
                  </th>
                  {providers.map((p) => (
                    <th
                      key={p.id}
                      className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide"
                    >
                      {p.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {criteria.map((c) => (
                  <tr key={c} className="border-t border-slate-100 align-top">
                    <td className="px-3 py-2.5 font-medium text-slate-800">{LABELS[c] || c}</td>
                    <td className="px-3 py-2.5 text-right font-semibold text-slate-700">
                      {f3(applied[c] ?? 0)}
                    </td>
                    {providers.map((p) => {
                      const cell = p.cells[c];
                      return (
                        <td key={p.id} className="px-3 py-2.5 text-right">
                          {cell?.performance === undefined ? (
                            <span className="text-slate-400">sem evidência</span>
                          ) : (
                            <>
                              <span className="block text-slate-800">
                                nota {f3(cell.performance)}
                              </span>
                              <span className="block text-[11px] text-slate-400">
                                parcela {f3(cell.contribution)}
                              </span>
                            </>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
              <tfoot className="tabular-nums">
                <tr className="border-t border-slate-200 bg-slate-50">
                  <td className="px-3 py-2.5 font-semibold text-slate-800" colSpan={2}>
                    Pontuação final
                  </td>
                  {providers.map((p) => (
                    <td key={p.id} className="px-3 py-2.5 text-right font-bold text-slate-900">
                      {f3(p.score)}
                    </td>
                  ))}
                </tr>
              </tfoot>
            </table>
          </div>

          <ul className="mt-2 space-y-0.5 font-mono text-[12px] text-slate-500">
            {providers.map((p) => (
              <li key={p.id}>
                <span className="font-sans font-medium text-slate-700">{p.name}</span> ={" "}
                {criteria
                  .filter((c) => p.cells[c]?.performance !== undefined)
                  .map((c) => `${f3(applied[c] ?? 0)} × ${f3(p.cells[c].performance as number)}`)
                  .join(" + ")}{" "}
                = <strong className="text-slate-800">{f3(p.score)}</strong>
              </li>
            ))}
          </ul>

          {shiftedDimensions.length > 0 && (
            <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-relaxed text-amber-900">
              <p>
                <strong>O peso aplicado não é o mesmo que você definiu no AHP.</strong> Indicadores
                sem evidência comparável saem da conta para todos os provedores, e o peso deles é
                redistribuído entre todos os indicadores que ficaram, de todas as dimensões.
                Dimensões que perderam mais indicadores perdem peso:
              </p>
              <ul className="mt-1.5 space-y-0.5 tabular-nums">
                {shiftedDimensions.map((c) => (
                  <li key={c}>
                    {LABELS[c] || c}: <strong>{pct(dimWeights[c] ?? 0)}</strong> no AHP →{" "}
                    <strong>{pct(applied[c] ?? 0)}</strong> aplicado ({indicatorsValid(c)} de{" "}
                    {indicatorsTotal(c)} indicadores com evidência comparável)
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* Camada 2 — de onde vem a diferença entre os provedores */}
        <div>
          <h4 className="mb-2 text-sm font-semibold text-slate-800">
            2. O que separou os provedores
          </h4>
          {decisive.length === 0 ? (
            <p className="text-sm leading-relaxed text-slate-600">
              Todos os {valid.length} indicadores comparáveis deram a mesma nota a todos os
              provedores. A diferença de pontuação é nula.
            </p>
          ) : (
            <>
              <p className="mb-2 text-sm leading-relaxed text-slate-600">
                {equivalentes.length > 0 ? (
                  <>
                    A pontuação usa {valid.length} indicador
                    {valid.length === 1 ? "" : "es"}: {valid.length === 1 ? "é o" : "são os"} que
                    {valid.length === 1 ? " deu" : " deram"} notas diferentes entre os provedores.
                    Outro{equivalentes.length === 1 ? "" : "s"} {equivalentes.length} reuni
                    {equivalentes.length === 1 ? "u" : "ram"} evidência comparável mas
                    {equivalentes.length === 1 ? " deu" : " deram"} a mesma nota aos três — como
                    somam a mesma parcela a todas as pontuações, não alteram a ordem e ficam fora
                    da soma. A nota de cada um continua na tabela acima.
                  </>
                ) : (
                  <>
                    Dos {valid.length} indicadores comparáveis, só{" "}
                    {decisive.length === 1
                      ? "este deu notas diferentes"
                      : `estes ${decisive.length} deram notas diferentes`}
                    . Os outros {valid.length - decisive.length} deram a mesma nota a todos e não
                    mudam a ordem.
                  </>
                )}
              </p>
              <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
                <table className="w-full min-w-[36rem] text-sm">
                  <thead className="bg-slate-50 text-slate-500">
                    <tr>
                      <th className="px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide">
                        Indicador
                      </th>
                      <th className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide">
                        Peso
                      </th>
                      {providers.map((p) => (
                        <th
                          key={p.id}
                          className="px-3 py-2 text-right text-xs font-semibold uppercase tracking-wide"
                        >
                          {p.name}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="tabular-nums">
                    {decisive.map((d) => (
                      <tr key={d.id} className="border-t border-slate-100 align-top">
                        <td className="px-3 py-2.5 font-medium text-slate-800">{d.name}</td>
                        <td className="px-3 py-2.5 text-right text-slate-700">{f3(d.weight)}</td>
                        {d.rows.map((row, i) => {
                          if (!row || row.contribution === null || row.normalized_value === null) {
                            return (
                              <td key={providers[i].id} className="px-3 py-2.5 text-right text-slate-400">
                                —
                              </td>
                            );
                          }
                          const best = d.bestContribution - row.contribution <= SAME_VALUE_EPS;
                          return (
                            <td key={providers[i].id} className="px-3 py-2.5 text-right">
                              <span className="block text-[11px] text-slate-400">
                                {shownValue(row)} → nota {f3(row.normalized_value)}
                              </span>
                              <span
                                className={
                                  "block " + (best ? "font-bold text-emerald-700" : "text-slate-700")
                                }
                              >
                                {/* 4 casas: diferenças como 99,9% × 99,99% somem em 3 */}
                                parcela {f4(row.contribution)}
                              </span>
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        {/* Camada 3 — regra de empate aplicada a este ranking */}
        {tieTolerance !== undefined &&
          synthesis.tie_break_policy === "show_tie" &&
          providers.length > 1 && (
          <TieRule providers={providers} tolerance={tieTolerance} />
        )}

        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
        >
          {open ? "Ocultar" : "Ver"} memória de cálculo completa
        </button>
      </div>

      {open && (
        <div className="space-y-5 border-t border-slate-100 bg-slate-50/60 p-5">
          <div className="rounded-xl border border-slate-200 bg-white p-4">
            <h4 className="mb-2 text-sm font-semibold text-slate-800">
              Como cada indicador vira nota
            </h4>
            <ul className="space-y-2 text-sm leading-relaxed text-slate-600">
              <li>
                <strong className="text-slate-800">Indicadores numéricos</strong> são comparados
                com o melhor valor observado. Quando maior é melhor (disponibilidade): valor ÷
                maior valor. Quando menor é melhor (PUE, latência): menor valor ÷ valor.
              </li>
              <li>
                <strong className="text-slate-800">Indicadores qualitativos</strong> usam a escala
                fixa da rubrica: baixo 0,25 · moderado 0,50 · alto 0,75 · completo 1,00.
              </li>
              <li>
                <strong className="text-slate-800">Peso de cada indicador</strong> = peso da
                dimensão (AHP) × peso dentro da dimensão (sua resposta de relevância), reajustado
                para que os indicadores comparáveis somem 1 (verificação:{" "}
                <span className="tabular-nums">{f3(synthesis.effective_weight_sum)}</span>).
              </li>
            </ul>
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
                              {row.source_year ? ` · ${row.source_year}` : ""}
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

/**
 * A regra de empate aplicada a este ranking, provedor a provedor.
 *
 * Reproduz a leitura de `compute_scores`: cada provedor é comparado com o
 * **primeiro do grupo** anterior, não com o vizinho. A posição vem do backend —
 * aqui só se mostra a distância que a justificou.
 */
function TieRule({
  providers,
  tolerance,
}: {
  providers: SynthesisProvider[];
  tolerance: number;
}) {
  let lider = providers[0];

  return (
    <div>
      <h4 className="mb-2 text-sm font-semibold text-slate-800">3. Posição e empate</h4>
      <p className="mb-2 text-sm leading-relaxed text-slate-600">
        Diferenças de até <strong className="tabular-nums">{f3(tolerance)}</strong> ponto contam
        como empate: são menores que o menor passo que a régua consegue medir. Cada provedor é
        comparado com o primeiro colocado do grupo acima dele.
      </p>
      <ul className="space-y-1 text-sm text-slate-700">
        {providers.slice(1).map((p) => {
          const referencia = lider;
          const empatado = p.rank === referencia.rank;
          if (!empatado) lider = p;
          return (
            <li key={p.id} className="tabular-nums">
              {p.name} fica <strong>{f3(referencia.score - p.score)}</strong> abaixo de{" "}
              {referencia.name} →{" "}
              {empatado ? (
                <span className="font-semibold text-amber-700">empate em {p.rank}º</span>
              ) : (
                <span className="font-semibold text-slate-900">
                  acima da margem, fica em {p.rank}º
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
