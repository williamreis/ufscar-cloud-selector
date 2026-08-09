import { useState } from "react";
import { Navigate, Link } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import Stepper from "../components/Stepper";
import EvidenceCard from "../components/EvidenceCard";
import AhpAudit from "../components/AhpAudit";
import { useAppState } from "../AppContext";

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
  oracle: "#eda100",
  ibm: "#e87ba4",
};
const FALLBACK_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"];

function providerColor(id: string, idx: number): string {
  return PROVIDER_COLORS[id] || FALLBACK_COLORS[idx % FALLBACK_COLORS.length];
}

const fmt3 = (v: unknown) => Number(v).toFixed(3);
const fmtPct = (v: unknown) => `${(Number(v) * 100).toFixed(0)}%`;

const MEDALS = ["🥇", "🥈", "🥉"];

export default function Results() {
  const { result } = useAppState();
  const [openProvider, setOpenProvider] = useState<string | null>(null);

  if (!result) return <Navigate to="/questionnaire" replace />;

  const {
    ranking,
    criteria_weights: cw,
    provider_scores: providerScores,
    notes,
    evidences,
    ahp,
    coverage,
  } = result;
  const scoresUnsourced = coverage?.scores_provenance?.status === "unsourced_placeholder";
  // As prioridades do AHP (modo distributivo) somam 1 entre os provedores, então
  // ficam na casa de 1/n — o eixo precisa acompanhar essa escala, não [0,1].
  const maxScore = Math.max(...ranking.map((r) => r.score));
  const scoreAxisMax = Math.min(1, Math.ceil(maxScore * 12) / 10);
  const top = ranking[0];
  const topCriterion = Object.entries(cw).sort((a, b) => b[1] - a[1])[0]?.[0];
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
    const row: Record<string, string | number> = { criterio: CRITERIA_LABELS[c] };
    providerScores.forEach((p) => {
      row[p.name] = (p[c] as number) ?? 0;
    });
    return row;
  });

  return (
    <div>
      <Stepper current="results" />

      <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-slate-900 via-slate-800 to-indigo-900 text-white p-8 mb-6 shadow-xl">
        <div className="absolute -right-20 -top-20 h-64 w-64 rounded-full bg-indigo-500/20 blur-3xl" />
        <div className="relative">
          <h1 className="text-2xl font-bold mb-2">Relatório da recomendação</h1>
          <p className="text-slate-300 text-sm max-w-2xl">
            Ranking, pesos dos critérios e evidências gerados a partir do seu questionário,
            combinando AHP (decisão multicritério) e IA generativa com RAG.
          </p>
        </div>
      </div>

      {/* Destaque do resultado */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-10">
        <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-blue-600 to-indigo-700 p-5 text-white shadow-lg shadow-blue-600/20">
          <div className="text-[11px] font-medium uppercase tracking-wider text-blue-100">
            Provedor recomendado
          </div>
          <div className="mt-1 flex items-center gap-2 text-2xl font-extrabold">
            <span aria-hidden>🏆</span>
            {top.name}
          </div>
        </div>
        <MetricCard
          label="Prioridade AHP"
          value={top.score.toFixed(3)}
          hint="As prioridades somam 1 entre os provedores"
        />
        <MetricCard
          label="Critério mais priorizado"
          value={`${CRITERIA_ICONS[topCriterion || ""] || ""} ${CRITERIA_LABELS[topCriterion || ""] || "—"}`}
        />
      </div>

      {ahp && (
        <div className="mb-10">
          <AhpAudit ahp={ahp} weights={cw} />
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-[1.3fr_1fr] gap-8 mb-10">
        <section>
          <SectionTitle
            step="a"
            title="Ranking dos provedores"
            desc="Ranking final obtido pelo método AHP, sintetizando o desempenho global de cada alternativa nos critérios avaliados."
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
                  {MEDALS[i] || <span className="text-sm font-bold text-slate-400">{r.rank}º</span>}
                </span>
                <span className="font-semibold text-slate-900">{r.name}</span>
                <span className="ml-auto rounded-lg bg-slate-900 px-2.5 py-1 font-mono text-xs font-semibold text-white">
                  {r.score.toFixed(3)}
                </span>
              </div>
            ))}
          </div>

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
                <Bar
                  dataKey="score"
                  radius={[0, 4, 4, 0]}
                  barSize={18}
                  label={{ position: "right", fontSize: 11, fill: "#52514e", formatter: fmt3 }}
                >
                  {rankData.map((entry) => {
                    const idx = ranking.findIndex((x) => x.id === entry.id);
                    return <Cell key={entry.id} fill={providerColor(entry.id, idx)} />;
                  })}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        </section>

        <section>
          <SectionTitle
            step="b"
            title="Importância dos critérios"
            desc="Pesos definidos a partir das respostas do questionário (escalas fechadas + análise de texto livre pela IA)."
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

      <section className="mb-10">
        <SectionTitle
          step="c"
          title="Comparativo dos provedores por critério"
          desc="Nota de cada provedor (0 a 1) em cada critério avaliado."
        />

        {scoresUnsourced && (
          <div className="mb-3 flex gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900">
            <span aria-hidden>⚠️</span>
            <p className="leading-relaxed">
              <strong>Atenção — notas sem fonte.</strong> {coverage?.scores_provenance.summary} Elas
              não são extraídas dos documentos indexados: o RAG alimenta apenas a seção de
              evidências. Portanto, a <strong>ordem do ranking</strong> decorre destes valores de
              referência, não dos relatórios oficiais. Os pesos dos critérios (seção B), esses sim,
              são calculados a partir das suas respostas e podem ser auditados acima.
            </p>
          </div>
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
              {providersWithoutEvidence.length === 1 ? "não possui" : "não possuem"} documentos
              indexados nesta base, portanto não há trechos a citar. Isso{" "}
              <strong>não afeta o ranking</strong> — as notas por critério vêm da base de
              referência dos provedores, não dos documentos. Para gerar evidências, adicione os
              relatórios desses provedores em <code className="rounded bg-amber-100 px-1">data/pdf</code>{" "}
              (com o nome do provedor no nome do arquivo) ou anexe-os na sua sessão.
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

      <div className="flex gap-3">
        <Link
          to="/questionnaire"
          className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
        >
          ← Refazer o questionário
        </Link>
        <button
          onClick={() => window.print()}
          className="rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
        >
          🖨 Imprimir / salvar PDF
        </button>
      </div>
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
