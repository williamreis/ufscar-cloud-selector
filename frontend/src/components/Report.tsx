/**
 * Corpo do relatório de recomendação.
 *
 * Extraído da página /results para que a área de gestão reexiba um envio antigo
 * exatamente como o gestor o viu, a partir do `response_json` gravado — sem
 * manter duas versões da mesma tela em sincronia manual.
 */
import { useState } from "react";
import type { RecommendationResponse } from "../types";
import QuestionnaireAnswers, { type AnsweredQuestion } from "./QuestionnaireAnswers";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import EvidenceCard from "./EvidenceCard";
import AhpAudit from "./AhpAudit";
import IndicatorWeights from "./IndicatorWeights";
import SynthesisAudit from "./SynthesisAudit";

const CRITERIA_LABELS: Record<string, string> = {
  sustainability: "Sustentabilidade",
  performance: "Desempenho",
  security: "Segurança",
};
const CRITERIA_COLORS: Record<string, string> = {
  sustainability: "#1baf7a",
  performance: "#2a78d6",
  security: "#4a3aa7",
};
const CRITERIA_ICONS: Record<string, string> = {
  sustainability: "🌱",
  performance: "⚡",
  security: "🔒",
};

// Cor fixa por identidade do provedor (nunca pelo rank) — assim a mesma cor
// significa o mesmo provedor em todos os gráficos, mesmo se a ordem mudar.
const PROVIDER_COLORS: Record<string, string> = {
  aws: "#2a78d6",
  gcp: "#eb6834",
  azure: "#1baf7a",
};
const FALLBACK_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"];

function providerColor(id: string, idx: number): string {
  return PROVIDER_COLORS[id] || FALLBACK_COLORS[idx % FALLBACK_COLORS.length];
}

const fmt3 = (v: unknown) => Number(v).toFixed(3);
const fmtPct = (v: unknown) => `${(Number(v) * 100).toFixed(0)}%`;

const REPORT_PARTS = [
  {
    title: "Ranking",
    text: "Pontuação de cada provedor e o desempenho comparado por dimensão.",
  },
  {
    title: "Pesos e cálculo",
    text: "Pesos das dimensões e dos indicadores, com a memória de cálculo do AHP e da agregação.",
  },
  {
    title: "Evidências",
    text: "Os trechos dos documentos que sustentam cada nota, com arquivo e página.",
  },
];

const MEDALS = ["🥇", "🥈", "🥉"];

// A medalha vem da **posição calculada**, não da ordem da lista. Com `show_tie`
// o backend dá a mesma posição a pontuações iguais, e indexar pelo array
// transformava três primeiros lugares em 🥇🥈🥉 — um desempate que ninguém
// calculou, que é exatamente o que a §12 proíbe.
function medalFor(rank: number): string | null {
  return MEDALS[rank - 1] ?? null;
}
export default function Report({
  result,
  answers,
  reviewHref,
}: {
  result: RecommendationResponse;
  /** Respostas do questionário. Omitidas quando a origem não as tem à mão. */
  answers?: AnsweredQuestion[];
  /** Para onde leva "Revisar comparações" quando o RC passa do limite de Saaty. */
  reviewHref?: string;
}) {
  const [openProvider, setOpenProvider] = useState<string | null>(null);

  const {
    ranking,
    criteria_weights: cw,
    provider_scores: providerScores,
    notes,
    evidences,
    ahp,
    synthesis,
    sensitivity,
    indicator_weights: indicatorWeights,
    coverage,
    submission_id: submissionId,
  } = result;
  const scoresUnsourced = coverage?.scores_provenance?.status === "unsourced_placeholder";
  const evidenceCoverage = coverage?.evidence;
  // Com RC acima do limite de Saaty os pesos saem de julgamentos que se
  // contradizem. O ranking continua sendo exibido — escondê-lo não ajudaria a
  // revisar —, mas como resultado preliminar, não como recomendação fechada.
  const preliminary = ahp ? !ahp.is_consistent : false;

  // As comparações do bloco D chegam estruturadas (preferência + intensidade) e
  // não têm `choice`; a frase legível de cada uma é a que o backend já gravou no
  // julgamento. Sem isto, as perguntas 17–19 apareceriam como não respondidas na
  // lista de respostas — justamente as que definiram os pesos.
  const pairwiseTexts = Object.fromEntries(
    Object.values(ahp?.judgments ?? {})
      .filter((j) => j.question_id && j.choice)
      .map((j) => [j.question_id as string, j.choice as string]),
  );
  // A pontuação da Equação 5 fica em [0,1], mas raramente encosta em 1 — o eixo
  // acompanha o maior score para que a diferença entre os provedores apareça.
  const maxScore = Math.max(...ranking.map((r) => r.score));
  const scoreAxisMax = Math.min(1, Math.ceil(maxScore * 12) / 10);
  const top = ranking[0];
  // Quem dividiu o primeiro lugar. Anunciar "provedor recomendado" com três
  // empatados apresentaria como escolha o que o cálculo não decidiu.
  const topTied = ranking.filter((r) => r.rank === top.rank);
  const hasTopTie = topTied.length > 1;
  // Critérios que dividem o topo. Com `sort` estável e pesos iguais, pegar
  // apenas o primeiro devolvia a ordem do objeto como se fosse prioridade: quem
  // marcou indiferença nas três comparações lia de volta que priorizou
  // sustentabilidade, só por ela ser a primeira dimensão declarada.
  const criteriaByWeight = Object.entries(cw).sort((a, b) => b[1] - a[1]);
  const topWeight = criteriaByWeight[0]?.[1];
  const topCriteria = criteriaByWeight
    .filter(([, w]) => topWeight !== undefined && Math.abs(w - topWeight) <= 1e-6)
    .map(([k]) => k);
  const criteriaTied = topCriteria.length > 1;
  const allCriteriaTied = criteriaTied && topCriteria.length === criteriaByWeight.length;
  const criterionLabel = (k: string) =>
    `${CRITERIA_ICONS[k] || ""} ${CRITERIA_LABELS[k] || k}`.trim();
  const providerName = (id: string) => ranking.find((r) => r.id === id)?.name || id;
  const providersWithoutEvidence = Object.entries(evidences)
    .filter(([, docs]) => docs.length === 0)
    .map(([id]) => id);

  const rankData = [...ranking].sort((a, b) => a.score - b.score);
  const weightsData = Object.entries(cw).map(([k, v]) => ({
    name: CRITERIA_LABELS[k] || k,
    value: v,
    key: k,
  }));
  const critCols = Object.keys(CRITERIA_LABELS).filter((c) => providerScores.some((p) => c in p));
  const comparisonData = critCols.map((c) => {
    // Ausência de desempenho medido fica `null`, não `0` (§11): zero afirmaria
    // "o provedor não atende", que é justamente o que não se sabe. O Recharts
    // omite a barra em vez de desenhá-la no chão.
    const row: Record<string, string | number | null> = { criterio: CRITERIA_LABELS[c] };
    providerScores.forEach((p) => {
      const valor = p[c];
      row[p.name] = typeof valor === "number" ? valor : null;
    });
    return row;
  });

  return (
    <div>
      <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-slate-900 via-slate-800 to-indigo-900 text-white p-8 mb-6 shadow-xl">
        <div className="absolute -right-20 -top-20 h-64 w-64 rounded-full bg-indigo-500/20 blur-3xl" />
        <div className="relative">
          <h1 className="text-2xl font-bold mb-2">Relatório da recomendação</h1>
          <p className="text-slate-300 text-sm max-w-3xl">
            Como as prioridades que você declarou se traduzem em ordem entre os provedores. Nada
            aqui é conclusão fechada: cada número tem o cálculo e a fonte à vista, e a decisão
            permanece sua.
          </p>

          <ul className="mt-5 grid gap-3 sm:grid-cols-3">
            {REPORT_PARTS.map((part) => (
              <li
                key={part.title}
                className="rounded-2xl border border-white/10 bg-white/5 px-4 py-3 backdrop-blur-sm"
              >
                <span className="block text-sm font-semibold text-white">{part.title}</span>
                <span className="mt-0.5 block text-xs leading-snug text-slate-400">
                  {part.text}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {/* Destaque do resultado */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-10">
        <div
          className={
            "relative overflow-hidden rounded-2xl p-5 text-white shadow-lg " +
            (preliminary
              ? "bg-gradient-to-br from-amber-600 to-amber-700 shadow-amber-600/20"
              : "bg-gradient-to-br from-blue-600 to-indigo-700 shadow-blue-600/20")
          }
        >
          <div
            className={
              "text-[11px] font-medium uppercase tracking-wider " +
              (preliminary ? "text-amber-100" : "text-blue-100")
            }
          >
            {hasTopTie
              ? `Empate em 1º lugar (${topTied.length} provedores)`
              : preliminary
                ? "1º lugar (resultado preliminar)"
                : "Provedor recomendado"}
          </div>
          <div className="mt-1 flex items-center gap-2 text-2xl font-extrabold">
            <span aria-hidden>{hasTopTie ? "🤝" : preliminary ? "⚠️" : "🏆"}</span>
            {hasTopTie ? topTied.map((r) => r.name).join(" · ") : top.name}
          </div>
          {hasTopTie && (
            <div className="mt-1 text-[11px] leading-snug text-blue-100">
              Os indicadores com evidência comparável não separaram estes provedores. A ordem
              exibida abaixo não é uma classificação — não há desempate calculado.
            </div>
          )}
          {preliminary && (
            <div className="mt-1 text-[11px] leading-snug text-amber-100">
              Não é uma recomendação definitiva: o RC das suas comparações está acima de{" "}
              {ahp?.consistency_threshold}.
            </div>
          )}
        </div>
        {/*
          O número é a pontuação da Equação 5 — Σ (peso efetivo × desempenho
          normalizado) —, per-provedor e em [0,1]. Não é a prioridade do
          autovetor e não soma 1 entre os provedores.

          A régua tem duas metades, porque a normalização é por tipo de
          indicador: nos quantitativos o desempenho é medido contra o melhor
          valor observado; nos de rubrica é o próprio nível do Quadro 23. Logo
          1,000 exige o melhor valor em todos os quantitativos **e** `completo`
          em todos os qualitativos — e uma avaliação em que todos ficaram em
          `alto` fecha perto de 0,75, que é o que ela de fato constatou.
        */}
        <MetricCard
          label="Pontuação final"
          value={top.score.toFixed(3)}
          hint="Σ (peso do indicador × desempenho). 1,000 exige o melhor valor em todos os indicadores quantitativos e o nível máximo da rubrica em todos os qualitativos."
        />
        <MetricCard
          label={
            allCriteriaTied
              ? "Critérios com o mesmo peso"
              : criteriaTied
                ? "Critérios mais priorizados (empate)"
                : "Critério mais priorizado"
          }
          value={
            allCriteriaTied
              ? "⚖ Nenhum priorizado"
              : topCriteria.length
                ? topCriteria.map(criterionLabel).join(" · ")
                : "—"
          }
          hint={
            allCriteriaTied
              ? "As suas comparações par-a-par deram o mesmo peso às três dimensões."
              : criteriaTied
                ? "Estas dimensões receberam pesos iguais."
                : undefined
          }
        />
      </div>

      {/* Registro de auditoria: silêncio aqui esconderia um envio que não ficou gravado */}
      {submissionId === null ? (
        <div className="mb-6 flex gap-3 rounded-xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
          <span aria-hidden>⚠️</span>
          <p className="leading-relaxed">
            <strong>Este envio não foi gravado no registro de auditoria.</strong> O relatório
            abaixo é válido, mas não aparecerá na área de gestão. Verifique os logs do backend
            antes de usar este resultado como registro.
          </p>
        </div>
      ) : (
        submissionId && (
          <p className="mb-6 font-mono text-[11px] text-slate-400">
            Registro de auditoria: {submissionId}
          </p>
        )
      )}

      {/* A entrada que gerou tudo o que vem abaixo */}
      {answers && answers.length > 0 && (
        <div className="mb-4">
          <QuestionnaireAnswers answers={answers} derived={pairwiseTexts} />
        </div>
      )}

      {ahp && (
        <div className="mb-10">
          <AhpAudit ahp={ahp} weights={cw} reviewHref={reviewHref} />
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[1.3fr_1fr] gap-8 mb-10">
        <section>
          <SectionTitle
            step="a"
            title={preliminary ? "Ranking dos provedores (preliminar)" : "Ranking dos provedores"}
            desc={
              preliminary
                ? "Ranking obtido pelo método AHP a partir de julgamentos inconsistentes (RC acima do limite de Saaty). Revise as comparações do bloco D antes de tratá-lo como recomendação."
                : "Ranking final obtido pelo método AHP, sintetizando o desempenho global de cada alternativa nos critérios avaliados."
            }
          />

          <div className="space-y-2 mb-5">
            {ranking.map((r, i) => (
              <div
                key={r.id}
                className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm transition-all hover:shadow-md"
              >
                <span
                  className="h-8 w-1.5 rounded-full"
                  style={{ backgroundColor: providerColor(r.id, i) }}
                  aria-hidden
                />
                <span className="w-7 text-center text-lg" aria-hidden>
                  {medalFor(r.rank) || (
                    <span className="text-sm font-bold text-slate-400">{r.rank}º</span>
                  )}
                </span>
                <span className="font-semibold text-slate-900">{r.name}</span>
                {r.tied && (
                  <span className="rounded-md bg-amber-100 px-2 py-0.5 text-[11px] font-semibold text-amber-800">
                    empate em {r.rank}º
                  </span>
                )}
                <span className="ml-auto rounded-lg bg-slate-900 px-2.5 py-1 font-mono text-xs font-semibold text-white">
                  {r.score.toFixed(3)}
                </span>
              </div>
            ))}
          </div>

          {sensitivity && (
            <div
              className={
                "mb-5 rounded-xl border px-4 py-3 text-sm leading-relaxed " +
                (sensitivity.margin_within_tolerance || !sensitivity.robust
                  ? "border-amber-300 bg-amber-50 text-amber-900"
                  : "border-emerald-300 bg-emerald-50 text-emerald-900")
              }
            >
              <p className="font-semibold">
                {sensitivity.margin_within_tolerance
                  ? "Os provedores no topo estão tecnicamente empatados."
                  : sensitivity.robust
                    ? "O primeiro lugar é robusto às suas prioridades."
                    : "O primeiro lugar depende do peso que você deu às dimensões."}
              </p>
              <p className="mt-1">
                A diferença entre o 1º e o 2º colocados é de{" "}
                <strong>{sensitivity.margin.toFixed(3)}</strong> ponto
                {sensitivity.margin_within_tolerance && (
                  <> — abaixo da margem de indiferença de {sensitivity.tie_break_tolerance.toFixed(2)}</>
                )}
                .{" "}
                {sensitivity.robust ? (
                  <>
                    Nenhuma mudança no peso de uma dimensão, sozinha, troca quem lidera.
                  </>
                ) : (
                  <>
                    Bastaria mover o peso de{" "}
                    <strong>
                      {CRITERIA_LABELS[sensitivity.most_fragile_dimension || ""] ||
                        sensitivity.most_fragile_dimension}
                    </strong>{" "}
                    em{" "}
                    <strong>
                      {Math.abs((sensitivity.most_fragile_delta || 0) * 100).toFixed(1)} ponto
                      {Math.abs((sensitivity.most_fragile_delta || 0) * 100) >= 2 ? "s" : ""}
                    </strong>{" "}
                    percentuais para o topo mudar.
                  </>
                )}
              </p>
              <ul className="mt-2 space-y-0.5 text-[12px]">
                {sensitivity.dimensions.map((d) => (
                  <li key={d.dimension}>
                    {CRITERIA_ICONS[d.dimension] || ""} {CRITERIA_LABELS[d.dimension] || d.dimension}{" "}
                    <span className="font-mono">({fmtPct(d.weight)})</span> —{" "}
                    {d.flips ? (
                      <>
                        troca o líder para{" "}
                        <strong>{providerName(d.flip_leader || "")}</strong> com{" "}
                        {(d.flip_delta || 0) > 0 ? "+" : "−"}
                        {Math.abs((d.flip_delta || 0) * 100).toFixed(1)} p.p.
                      </>
                    ) : (
                      <>nenhum peso possível troca o líder</>
                    )}
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-[11px] opacity-80">
                Cada linha move o peso daquela dimensão e reescala as outras
                proporcionalmente, mantendo as suas demais respostas. É uma medida sobre o
                resultado: não altera pontuação, peso nem posição.
              </p>
            </div>
          )}

          <ChartCard>
            <ResponsiveContainer width="100%" height={230}>
              <BarChart data={rankData} layout="vertical" margin={{ left: 8, right: 44, top: 4, bottom: 4 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e1e0d9" horizontal={false} />
                <XAxis
                  type="number"
                  domain={[0, scoreAxisMax]}
                  stroke="#898781"
                  fontSize={11}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  stroke="#898781"
                  fontSize={11}
                  width={104}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip formatter={fmt3} cursor={{ fill: "rgba(15,23,42,0.04)" }} />
                {/* LabelList, e não o prop `label`: o recharts 3 removeu esse prop do
                    <Bar> e os rótulos vinham sendo silenciosamente ignorados.
                    isAnimationActive={false} porque o LabelList só é montado quando a
                    animação termina, e o callback de fim não dispara de forma confiável. */}
                <Bar dataKey="score" radius={[0, 4, 4, 0]} barSize={18} isAnimationActive={false}>
                  {rankData.map((entry) => {
                    const idx = ranking.findIndex((x) => x.id === entry.id);
                    return <Cell key={entry.id} fill={providerColor(entry.id, idx)} />;
                  })}
                  <LabelList
                    dataKey="score"
                    position="right"
                    formatter={fmt3}
                    fontSize={11}
                    fill="#52514e"
                  />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        </section>

        <section>
          <SectionTitle
            step="b"
            title="Importância dos critérios"
            desc="Pesos obtidos pelo autovetor da matriz de Saaty, montada com as suas comparações par-a-par (perguntas 17–19)."
          />
          <ChartCard>
            <ResponsiveContainer width="100%" height={250}>
              <PieChart>
                <Pie
                  data={weightsData}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={62}
                  outerRadius={96}
                  paddingAngle={2}
                >
                  {weightsData.map((entry) => (
                    <Cell
                      key={entry.key}
                      fill={CRITERIA_COLORS[entry.key] || "#64748b"}
                      stroke="#fff"
                      strokeWidth={2}
                    />
                  ))}
                </Pie>
                <Tooltip formatter={fmtPct} />
                <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
          </ChartCard>

          <div className="mt-3 space-y-2">
            {Object.entries(cw)
              .sort((a, b) => b[1] - a[1])
              .map(([k, v]) => (
                <div key={k} className="flex items-center gap-3 text-sm">
                  <span className="w-36 text-slate-600">
                    {CRITERIA_ICONS[k]} {CRITERIA_LABELS[k] || k}
                  </span>
                  <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200">
                    <div
                      className="h-full rounded-full transition-all duration-700"
                      style={{ width: `${v * 100}%`, backgroundColor: CRITERIA_COLORS[k] }}
                    />
                  </div>
                  <span className="w-10 text-right font-semibold text-slate-700">
                    {Math.round(v * 100)}%
                  </span>
                </div>
              ))}
          </div>
        </section>
      </div>

      {indicatorWeights && (
        <div className="mb-10">
          <IndicatorWeights weights={indicatorWeights} criteriaWeights={cw} />
        </div>
      )}

      <section className="mb-10">
        <SectionTitle
          step="c"
          title="Comparativo dos provedores por critério"
          desc="Desempenho de cada provedor (0 a 1) em cada dimensão, extraído dos documentos e já normalizado. Sem o peso da dimensão: mede desempenho, não prioridade."
        />

        {scoresUnsourced ? (
          <div className="mb-3 flex gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <span aria-hidden>⚠️</span>
            <p className="leading-relaxed">
              <strong>Atenção — notas sem fonte.</strong> {coverage?.scores_provenance.summary}
            </p>
          </div>
        ) : (
          evidenceCoverage && (
            <div className="mb-3 flex gap-3 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
              <span aria-hidden>📄</span>
              <p className="leading-relaxed">
                <strong>
                  {evidenceCoverage.indicators_in_comparison} de{" "}
                  {evidenceCoverage.indicators_requested} indicadores
                </strong>{" "}
                reuniram evidência comparável em todos os provedores e formaram o ranking. Os
                demais saíram da conta — para todos os provedores, sem penalizar ninguém — e
                aparecem com o motivo na memória de cálculo abaixo. Cada valor usado pode ser
                conferido no documento de origem.
              </p>
            </div>
          )
        )}

        <ChartCard>
          <ResponsiveContainer width="100%" height={330}>
            <BarChart data={comparisonData} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e1e0d9" vertical={false} />
              <XAxis dataKey="criterio" stroke="#898781" fontSize={12} tickLine={false} />
              <YAxis domain={[0, 1]} stroke="#898781" fontSize={11} tickLine={false} axisLine={false} />
              <Tooltip formatter={fmt3} cursor={{ fill: "rgba(15,23,42,0.04)" }} />
              <Legend iconType="circle" wrapperStyle={{ fontSize: 12 }} />
              {providerScores.map((p, idx) => (
                <Bar
                  key={p.id}
                  dataKey={p.name}
                  fill={providerColor(p.id, idx)}
                  radius={[4, 4, 0, 0]}
                  maxBarSize={30}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <div className="mt-4 overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th className="px-4 py-3 text-left font-semibold">#</th>
                <th className="px-4 py-3 text-left font-semibold">Provedor</th>
                <th className="px-4 py-3 text-left font-semibold">Score final</th>
                {critCols.map((c) => (
                  <th key={c} className="px-4 py-3 text-left font-semibold">
                    {CRITERIA_LABELS[c]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="tabular-nums">
              {providerScores.map((p, idx) => (
                <tr key={p.id} className="border-t border-slate-100 hover:bg-slate-50/60">
                  <td className="px-4 py-3 text-slate-400">{p.rank}</td>
                  <td className="px-4 py-3">
                    <span className="flex items-center gap-2 font-medium text-slate-800">
                      <span
                        className="h-2.5 w-2.5 rounded-full"
                        style={{ backgroundColor: providerColor(p.id, idx) }}
                        aria-hidden
                      />
                      {p.name}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-semibold text-slate-900">{p.score.toFixed(3)}</td>
                  {critCols.map((c) => (
                    <td key={c} className="px-4 py-3 text-slate-600">
                      {typeof p[c] === "number" ? (p[c] as number).toFixed(3) : "—"}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Fecha a lacuna entre a tabela e o número: como a nota vira score final */}
        {synthesis && (
          <div className="mt-4">
            <SynthesisAudit synthesis={synthesis} />
          </div>
        )}
      </section>

      <section className="mb-10">
        <SectionTitle step="d" title="Justificativa gerada pela IA" />
        <div className="rounded-2xl border border-blue-200/70 bg-gradient-to-br from-blue-50 to-indigo-50/50 p-5">
          <div className="flex gap-3">
            <span className="text-xl leading-none" aria-hidden>
              🧠
            </span>
            <p className="text-sm leading-relaxed text-slate-700">{notes}</p>
          </div>
        </div>
      </section>

      <section className="mb-8">
        <SectionTitle
          step="e"
          title="Evidências (trechos dos documentos)"
          desc="Trechos recuperados dos relatórios oficiais, agrupados por provedor e vinculados ao indicador que sustentam. Clique no nome do arquivo para abrir o PDF na página citada."
        />

        {providersWithoutEvidence.length > 0 && (
          <div className="mb-3 flex gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <span aria-hidden>ℹ️</span>
            <p className="leading-relaxed">
              <strong>{providersWithoutEvidence.map(providerName).join(" e ")}</strong>{" "}
              {providersWithoutEvidence.length === 1 ? "não possui" : "não possuem"} trechos
              recuperados nesta base. Como o desempenho é extraído dos documentos,{" "}
              <strong>os indicadores sem evidência saem da comparação</strong> — de todos os
              provedores, para que ninguém seja avaliado por uma régua diferente. Para ampliar a
              base, adicione os relatórios desses provedores em{" "}
              <code className="rounded bg-amber-100 px-1">data/pdf</code> (com o nome do provedor
              no nome do arquivo) ou anexe-os na sua sessão.
            </p>
          </div>
        )}

        <div className="space-y-2">
          {Object.entries(evidences).map(([providerId, docs]) => {
            const isOpen = openProvider === providerId;
            const idx = ranking.findIndex((r) => r.id === providerId);
            return (
              <div
                key={providerId}
                className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm"
              >
                <button
                  type="button"
                  onClick={() => setOpenProvider(isOpen ? null : providerId)}
                  aria-expanded={isOpen}
                  disabled={docs.length === 0}
                  className="flex w-full items-center gap-3 px-4 py-3.5 text-left transition hover:bg-slate-50 disabled:cursor-default disabled:hover:bg-transparent"
                >
                  <span
                    className="h-6 w-1.5 rounded-full"
                    style={{ backgroundColor: providerColor(providerId, idx) }}
                    aria-hidden
                  />
                  <span
                    className={docs.length ? "font-semibold text-slate-800" : "font-semibold text-slate-400"}
                  >
                    {providerName(providerId)}
                  </span>
                  {docs.length > 0 ? (
                    <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-500">
                      {docs.length} {docs.length === 1 ? "trecho" : "trechos"}
                    </span>
                  ) : (
                    <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-medium text-amber-700 ring-1 ring-inset ring-amber-600/20">
                      Sem documentos indexados
                    </span>
                  )}
                  {docs.length > 0 && (
                    <span
                      className={`ml-auto text-slate-400 transition-transform ${isOpen ? "rotate-180" : ""}`}
                      aria-hidden
                    >
                      ▾
                    </span>
                  )}
                </button>
                {isOpen && docs.length > 0 && (
                  <div className="space-y-2.5 border-t border-slate-100 bg-slate-50/50 p-4">
                    {docs.map((d, i) => (
                      <EvidenceCard key={i} ev={d} />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function SectionTitle({ step, title, desc }: { step: string; title: string; desc?: string }) {
  return (
    <header className="mb-3">
      <h2 className="flex items-center gap-2 text-lg font-bold text-slate-900">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-slate-900 text-[11px] font-bold uppercase text-white">
          {step}
        </span>
        {title}
      </h2>
      {desc && <p className="mt-1 text-sm leading-relaxed text-slate-500">{desc}</p>}
    </header>
  );
}

function ChartCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">{children}</div>
  );
}

function MetricCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="text-[11px] font-medium uppercase tracking-wider text-slate-400">{label}</div>
      <div className="mt-1 text-2xl font-extrabold text-slate-900">{value}</div>
      {hint && <div className="mt-0.5 text-[11px] text-slate-400">{hint}</div>}
    </div>
  );
}
