import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  AdminAuthError,
  adminDeleteSubmission,
  adminExportCsv,
  adminLogin,
  adminRagIngest,
  adminRagStatus,
  adminSessionValid,
  adminStats,
  adminSubmission,
  adminSubmissions,
  setAdminToken,
} from "../api";
import Report from "../components/Report";
import type {
  AdminStats,
  IngestResult,
  RagStatus,
  SubmissionDetail,
  SubmissionListItem,
} from "../types";

// Mesmas cores do relatório: um critério (ou provedor) tem a mesma cor em todo o
// sistema, e ela nunca muda com a posição no ranking.
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
const PROVIDER_COLORS: Record<string, string> = {
  aws: "#2a78d6",
  gcp: "#eb6834",
  azure: "#1baf7a",
  oracle: "#eda100",
  ibm: "#e87ba4",
};

const PAGE_SIZE = 20;

const pct = (v: number | null | undefined) =>
  v == null ? "—" : `${(v * 100).toFixed(1)}%`;
/** Versão para recharts, cujos formatters entregam o valor sem tipo garantido. */
const pctChart = (v: unknown) => `${(Number(v) * 100).toFixed(1)}%`;
const num = (v: number | null | undefined, d = 3) => (v == null ? "—" : v.toFixed(d));
const dateTime = (iso: string) => new Date(iso).toLocaleString("pt-BR");
const bytes = (n: number) =>
  n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
const dayLabel = (day: string) => {
  const [, m, d] = day.split("-");
  return `${d}/${m}`;
};

export default function Admin() {
  // null enquanto o token guardado ainda está sendo conferido no servidor
  const [authed, setAuthed] = useState<boolean | null>(null);
  // /admin → lista + dashboard · /admin/:id → um envio
  const { id } = useParams();

  useEffect(() => {
    adminSessionValid().then(setAuthed);
  }, []);

  if (authed === null) {
    return (
      <div className="flex flex-col items-center py-24 text-slate-400">
        <div className="mb-3 h-8 w-8 animate-spin rounded-full border-3 border-slate-200 border-t-blue-600" />
        Verificando sessão…
      </div>
    );
  }

  if (!authed) return <Login onSuccess={() => setAuthed(true)} />;

  const logout = () => {
    setAdminToken(null);
    setAuthed(false);
  };

  return id ? <SubmissionPage id={id} onLogout={logout} /> : <Dashboard onLogout={logout} />;
}

// ---------------------------------------------------------------------------

function Login({ onSuccess }: { onSuccess: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await adminLogin(password);
      setPassword("");
      onSuccess();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mx-auto max-w-sm py-10">
      <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h1 className="mb-1 text-xl font-bold text-slate-900">Área de gestão</h1>
        <p className="mb-5 text-sm leading-relaxed text-slate-500">
          Consulta dos questionários respondidos e dos resultados gerados, para auditoria.
          A senha é conferida no servidor.
        </p>
        <form onSubmit={submit}>
          <label htmlFor="admin-password" className="mb-1.5 block text-sm font-semibold text-slate-800">
            Senha de administrador
          </label>
          <input
            id="admin-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mb-3 w-full rounded-xl border border-slate-300 px-3.5 py-2.5 text-sm shadow-sm transition focus:border-blue-500 focus:outline-none focus:ring-4 focus:ring-blue-500/10"
          />
          <button
            type="submit"
            disabled={loading || !password}
            className="w-full rounded-xl bg-gradient-to-br from-blue-600 to-indigo-700 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-blue-600/25 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? "Entrando…" : "Entrar"}
          </button>
        </form>
        {error && (
          <p role="alert" className="mt-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function Dashboard({ onLogout }: { onLogout: () => void }) {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [items, setItems] = useState<SubmissionListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [toDelete, setToDelete] = useState<SubmissionListItem | null>(null);
  const [loading, setLoading] = useState(true);

  const handleError = useCallback(
    (err: unknown) => {
      if (err instanceof AdminAuthError) onLogout();
      else setError(err instanceof Error ? err.message : String(err));
    },
    [onLogout],
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, list] = await Promise.all([
        adminStats(),
        adminSubmissions(PAGE_SIZE, page * PAGE_SIZE, search),
      ]);
      setStats(s);
      setItems(list.items);
      setTotal(list.total);
      setError(null);
    } catch (err) {
      handleError(err);
    } finally {
      setLoading(false);
    }
  }, [page, search, handleError]);

  useEffect(() => {
    load();
  }, [load]);

  const weightsData = stats
    ? Object.keys(CRITERIA_LABELS).map((k) => ({
        key: k,
        name: CRITERIA_LABELS[k],
        value: stats.average_weights[k] ?? 0,
      }))
    : [];
  const providerData = stats?.top_provider_counts ?? [];
  const dayData = (stats?.submissions_by_day ?? []).map((d) => ({
    ...d,
    label: dayLabel(d.day),
  }));
  const consistentPct =
    stats && stats.total > 0 ? (stats.consistency.consistent / stats.total) * 100 : null;
  const lastPage = Math.max(0, Math.ceil(total / PAGE_SIZE) - 1);

  return (
    <div>
      <div className="relative mb-6 overflow-hidden rounded-3xl bg-gradient-to-br from-slate-900 via-slate-800 to-indigo-900 p-8 text-white shadow-xl">
        <div className="absolute -right-20 -top-20 h-64 w-64 rounded-full bg-indigo-500/20 blur-3xl" />
        <div className="relative flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="mb-2 text-2xl font-bold">Área de gestão</h1>
            <p className="max-w-2xl text-sm text-slate-300">
              Registro de auditoria dos questionários respondidos: quem respondeu, o que
              respondeu, quais pesos saíram do AHP e qual ranking foi gerado.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => adminExportCsv().catch(handleError)}
              className="rounded-xl bg-white/10 px-3.5 py-2 text-sm font-medium text-white ring-1 ring-white/20 transition hover:bg-white/20"
            >
              ⬇ Exportar CSV
            </button>
            <button
              onClick={onLogout}
              className="rounded-xl bg-white/10 px-3.5 py-2 text-sm font-medium text-white ring-1 ring-white/20 transition hover:bg-white/20"
            >
              Sair
            </button>
          </div>
        </div>
      </div>

      {error && (
        <div role="alert" className="mb-6 rounded-2xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {stats && stats.total === 0 ? (
        <div className="rounded-2xl border border-slate-200 bg-white p-10 text-center">
          <p className="text-lg font-semibold text-slate-800">Nenhum questionário registrado</p>
          <p className="mt-1 text-sm text-slate-500">
            Os envios passam a aparecer aqui assim que alguém concluir o questionário.
          </p>
        </div>
      ) : (
        <>
          <div className="mb-8 grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat label="Questionários respondidos" value={stats ? String(stats.total) : "—"} />
            <Stat
              label="Julgamentos consistentes"
              value={consistentPct == null ? "—" : `${consistentPct.toFixed(0)}%`}
              hint={stats ? `${stats.consistency.consistent} de ${stats.total} com RC ≤ 0,10` : undefined}
            />
            <Stat
              label="Razão de consistência média"
              value={num(stats?.consistency.average_ratio)}
              hint="Limite de Saaty: 0,10"
            />
            <Stat
              label="Mais recomendado"
              value={providerData[0]?.name || "—"}
              hint={providerData[0] ? `1º lugar em ${providerData[0].count} envio(s)` : undefined}
            />
          </div>

          <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <ChartCard
              title="Peso médio das dimensões"
              desc="Média dos pesos que o AHP produziu a partir das comparações par-a-par de cada respondente."
            >
              <ResponsiveContainer width="100%" height={200}>
                <BarChart
                  data={weightsData}
                  layout="vertical"
                  margin={{ left: 8, right: 52, top: 4, bottom: 4 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#e1e0d9" horizontal={false} />
                  <XAxis
                    type="number"
                    domain={[0, 1]}
                    tickFormatter={(v) => `${Math.round(v * 100)}%`}
                    stroke="#898781"
                    fontSize={11}
                    tickLine={false}
                  />
                  <YAxis
                    type="category"
                    dataKey="name"
                    stroke="#898781"
                    fontSize={11}
                    width={112}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip formatter={pctChart} cursor={{ fill: "rgba(15,23,42,0.04)" }} />
                  <Bar dataKey="value" radius={[0, 4, 4, 0]} barSize={18} isAnimationActive={false}>
                    {weightsData.map((d) => (
                      <Cell key={d.key} fill={CRITERIA_COLORS[d.key]} />
                    ))}
                    <LabelList
                      dataKey="value"
                      position="right"
                      formatter={pctChart}
                      fontSize={11}
                      fill="#52514e"
                    />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>

            <ChartCard
              title="Provedor em 1º lugar, por número de envios"
              desc="Quantas vezes cada provedor ficou no topo do ranking."
            >
              <ResponsiveContainer width="100%" height={200}>
                <BarChart
                  data={providerData}
                  layout="vertical"
                  margin={{ left: 8, right: 40, top: 4, bottom: 4 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#e1e0d9" horizontal={false} />
                  <XAxis
                    type="number"
                    allowDecimals={false}
                    stroke="#898781"
                    fontSize={11}
                    tickLine={false}
                  />
                  <YAxis
                    type="category"
                    dataKey="name"
                    stroke="#898781"
                    fontSize={11}
                    width={112}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip cursor={{ fill: "rgba(15,23,42,0.04)" }} />
                  <Bar dataKey="count" name="Envios" radius={[0, 4, 4, 0]} barSize={18} isAnimationActive={false}>
                    {providerData.map((d) => (
                      <Cell key={d.id} fill={PROVIDER_COLORS[d.id] || "#64748b"} />
                    ))}
                    <LabelList dataKey="count" position="right" fontSize={11} fill="#52514e" />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>

            <ChartCard
              title="Envios por dia"
              desc="Volume de respostas ao longo do tempo."
            >
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={dayData} margin={{ left: 0, right: 8, top: 12, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e1e0d9" vertical={false} />
                  <XAxis dataKey="label" stroke="#898781" fontSize={11} tickLine={false} />
                  <YAxis
                    allowDecimals={false}
                    stroke="#898781"
                    fontSize={11}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip cursor={{ fill: "rgba(15,23,42,0.04)" }} />
                  <Bar
                    dataKey="count"
                    name="Envios"
                    fill="#2a78d6"
                    radius={[4, 4, 0, 0]}
                    maxBarSize={28}
                    isAnimationActive={false}
                  >
                    <LabelList dataKey="count" position="top" fontSize={11} fill="#52514e" />
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </ChartCard>

            <ChartCard
              title="Cargos/funções que responderam"
              desc="Dez cargos mais frequentes entre os respondentes."
            >
              {stats && stats.roles.length > 0 ? (
                <ul className="divide-y divide-slate-100 text-sm">
                  {stats.roles.map((r) => (
                    <li key={r.role} className="flex items-center justify-between py-2">
                      <span className="text-slate-700">{r.role}</span>
                      <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                        {r.count}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="py-8 text-center text-sm text-slate-400">Nenhum cargo informado.</p>
              )}
            </ChartCard>
          </div>

          <section>
            <div className="mb-3 flex flex-wrap items-center gap-3">
              <h2 className="text-lg font-bold text-slate-900">Questionários respondidos</h2>
              <input
                type="search"
                value={search}
                onChange={(e) => {
                  setPage(0);
                  setSearch(e.target.value);
                }}
                placeholder="Buscar por e-mail ou cargo"
                className="ml-auto w-64 rounded-xl border border-slate-300 px-3.5 py-2 text-sm shadow-sm transition focus:border-blue-500 focus:outline-none focus:ring-4 focus:ring-blue-500/10"
              />
            </div>

            <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
              <table className="w-full min-w-[52rem] text-sm">
                <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-4 py-3 text-left font-semibold">Data</th>
                    <th className="px-4 py-3 text-left font-semibold">E-mail</th>
                    <th className="px-4 py-3 text-left font-semibold">Cargo/Função</th>
                    <th className="px-4 py-3 text-right font-semibold">Sust.</th>
                    <th className="px-4 py-3 text-right font-semibold">Desemp.</th>
                    <th className="px-4 py-3 text-right font-semibold">Seg.</th>
                    <th className="px-4 py-3 text-right font-semibold">RC</th>
                    <th className="px-4 py-3 text-left font-semibold">1º lugar</th>
                    <th className="px-4 py-3" />
                  </tr>
                </thead>
                <tbody className="tabular-nums">
                  {loading && items.length === 0 && (
                    <tr>
                      <td colSpan={9} className="px-4 py-8 text-center text-slate-400">
                        Carregando…
                      </td>
                    </tr>
                  )}
                  {!loading && items.length === 0 && (
                    <tr>
                      <td colSpan={9} className="px-4 py-8 text-center text-slate-400">
                        Nenhum envio corresponde à busca.
                      </td>
                    </tr>
                  )}
                  {items.map((s) => (
                    <tr key={s.id} className="border-t border-slate-100 hover:bg-slate-50/60">
                      <td className="whitespace-nowrap px-4 py-3 text-slate-500">
                        {dateTime(s.created_at)}
                      </td>
                      <td className="px-4 py-3 font-medium text-slate-800">{s.respondent_email}</td>
                      <td className="px-4 py-3 text-slate-600">{s.respondent_role || "—"}</td>
                      <td className="px-4 py-3 text-right text-slate-600">
                        {pct(s.weights.sustainability)}
                      </td>
                      <td className="px-4 py-3 text-right text-slate-600">
                        {pct(s.weights.performance)}
                      </td>
                      <td className="px-4 py-3 text-right text-slate-600">
                        {pct(s.weights.security)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <span
                          className={
                            s.is_consistent
                              ? "font-medium text-emerald-600"
                              : "font-medium text-amber-600"
                          }
                          title={s.is_consistent ? "Consistente" : "Acima do limite de 0,10"}
                        >
                          {s.is_consistent ? "✓" : "⚠"} {num(s.consistency_ratio)}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="flex items-center gap-2 text-slate-700">
                          <span
                            className="h-2.5 w-2.5 rounded-full"
                            style={{
                              backgroundColor: PROVIDER_COLORS[s.top_provider_id || ""] || "#64748b",
                            }}
                            aria-hidden
                          />
                          {s.top_provider_name || "—"}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-1.5">
                          <Link
                            to={`/admin/${s.id}`}
                            className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 transition hover:bg-slate-50"
                          >
                            Ver
                          </Link>
                          <button
                            onClick={() => setToDelete(s)}
                            aria-label={`Excluir o envio de ${s.respondent_email}`}
                            title="Excluir envio"
                            className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-500 transition hover:border-red-300 hover:bg-red-50 hover:text-red-700"
                          >
                            Excluir
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {total > PAGE_SIZE && (
              <div className="mt-3 flex items-center gap-3 text-sm text-slate-500">
                <button
                  disabled={page === 0}
                  onClick={() => setPage((p) => p - 1)}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
                >
                  ← Anterior
                </button>
                <span>
                  Página {page + 1} de {lastPage + 1} · {total} envios
                </span>
                <button
                  disabled={page >= lastPage}
                  onClick={() => setPage((p) => p + 1)}
                  className="rounded-lg border border-slate-300 px-3 py-1.5 transition hover:bg-slate-50 disabled:opacity-40"
                >
                  Próxima →
                </button>
              </div>
            )}
          </section>
        </>
      )}

      <RagPanel onError={handleError} />

      {toDelete && (
        <ConfirmDelete
          submission={toDelete}
          onCancel={() => setToDelete(null)}
          onDeleted={() => {
            setToDelete(null);
            // Se a página ficou vazia por ter perdido o último item, volta uma.
            if (items.length === 1 && page > 0) setPage((p) => p - 1);
            else load();
          }}
          onError={handleError}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

/**
 * Base documental do RAG: o que está em data/pdf, o que já foi indexado e o
 * botão que roda a ingestão.
 *
 * Três fontes distintas são mostradas lado a lado porque elas podem discordar, e
 * a discordância é a informação útil: o **diretório** (arquivo novo no servidor),
 * a tabela de **documentos** (o que já foi ingerido) e o **índice FAISS** (o que
 * de fato responde às buscas). Nada aqui é deduzido de nada — um documento
 * registrado com o índice apagado aparece como índice ausente, não como pronto.
 */
function RagPanel({ onError }: { onError: (err: unknown) => void }) {
  const [status, setStatus] = useState<RagStatus | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [result, setResult] = useState<IngestResult | null>(null);
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await adminRagStatus());
    } catch (err) {
      onError(err);
    } finally {
      setLoading(false);
    }
  }, [onError]);

  useEffect(() => {
    load();
  }, [load]);

  async function ingest(files?: string[]) {
    setRunning(true);
    setResult(null);
    try {
      const ingestion = await adminRagIngest(files);
      setResult(ingestion);
      setSelected([]);
      await load();
    } catch (err) {
      onError(err);
    } finally {
      setRunning(false);
    }
  }

  const files = status?.files ?? [];
  const toggle = (name: string) =>
    setSelected((current) =>
      current.includes(name) ? current.filter((n) => n !== name) : [...current, name],
    );

  return (
    <section id="base-documental" className="mt-10">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-slate-900">Base documental (RAG)</h2>
          <p className="mt-1 max-w-3xl text-sm text-slate-500">
            Documentos oficiais em <code className="rounded bg-slate-100 px-1">data/pdf</code>,
            indexados com escopo global e consultados em <strong>todas</strong> as buscas de
            evidência. Reingerir um arquivo já indexado <strong>acrescenta os trechos dele ao
            índice outra vez</strong> — o registro no banco é atualizado (o id do documento é o
            hash do conteúdo), mas os vetores duplicam. Prefira marcar só os pendentes.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => load()}
            disabled={loading || running}
            className="rounded-xl border border-slate-300 px-3.5 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
          >
            ⟳ Atualizar
          </button>
          <button
            onClick={() => ingest(selected.length ? selected : undefined)}
            disabled={running || loading || files.length === 0}
            className="rounded-xl bg-gradient-to-br from-blue-600 to-indigo-700 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-blue-600/25 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {running
              ? "Indexando…"
              : selected.length
                ? `Executar ingestão (${selected.length} selecionado${selected.length > 1 ? "s" : ""})`
                : "Executar ingestão de todos"}
          </button>
        </div>
      </div>

      {running && (
        <p className="mb-3 text-sm text-slate-500">
          A ingestão lê, fatia e calcula os embeddings de cada arquivo — em documentos longos
          leva alguns minutos. Não feche a aba.
        </p>
      )}

      <div className="mb-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat
          label="Trechos indexados"
          value={status ? String(status.chunks_total) : "—"}
          hint={status ? `${status.documents_indexed} documento(s) registrado(s)` : undefined}
        />
        <Stat
          label="Arquivos em data/pdf"
          value={status ? String(files.length) : "—"}
          hint={status ? `${status.pending_files} ainda não indexado(s)` : undefined}
        />
        <Stat
          label="Modelo de embeddings"
          value={status?.embedding_model || "—"}
          hint={status ? status.embedding_provider : undefined}
        />
        <Stat
          label="Fatiamento"
          value={status ? `${status.chunk_size}` : "—"}
          hint={status ? `sobreposição de ${status.chunk_overlap} caracteres` : undefined}
        />
      </div>

      {status && !status.index_ready && (
        <div className="mb-4 rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Nenhum índice construído ainda: as buscas de evidência não têm o que recuperar e
          nenhum provedor entra no ranking. Execute a ingestão.
        </div>
      )}

      {status && status.index_ready && status.chunks_total > 0 && status.documents_indexed === 0 && (
        <div className="mb-4 rounded-2xl border border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-700">
          O índice tem {status.chunks_total} trechos, mas <strong>nenhum documento
          registrado</strong>: esta base foi indexada antes de a tabela de documentos existir
          (ou por fora da aplicação). Os arquivos abaixo aparecem como pendentes por falta de
          registro, não por falta de índice — reingerir todos duplicaria os vetores já presentes.
        </div>
      )}

      {status && status.unassigned_files.length > 0 && (
        <div className="mb-4 rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <strong>{status.unassigned_files.length} arquivo(s) sem provedor identificável no
          nome</strong> ({status.unassigned_files.join(", ")}). Eles são indexados, mas não viram
          evidência de nenhum provedor — inclua o nome do provedor no nome do arquivo (ex.:{" "}
          <code>aws-sustentabilidade-2025.pdf</code>) e reingira.
        </div>
      )}

      <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="w-full min-w-[46rem] text-sm">
          <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="w-10 px-4 py-3" />
              <th className="px-4 py-3 text-left font-semibold">Arquivo</th>
              <th className="px-4 py-3 text-left font-semibold">Provedor</th>
              <th className="px-4 py-3 text-right font-semibold">Ano</th>
              <th className="px-4 py-3 text-right font-semibold">Tamanho</th>
              <th className="px-4 py-3 text-left font-semibold">Situação</th>
              <th className="px-4 py-3 text-right font-semibold">Trechos</th>
              <th className="px-4 py-3 text-left font-semibold">Última ingestão</th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {loading && files.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-slate-400">
                  Carregando…
                </td>
              </tr>
            )}
            {!loading && files.length === 0 && (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-slate-500">
                  Nenhum documento em <code>{status?.pdf_dir}</code>. Coloque os arquivos{" "}
                  {(status?.allowed_extensions ?? []).join(" ou ")} nessa pasta do servidor e
                  atualize.
                </td>
              </tr>
            )}
            {files.map((f) => (
              <tr key={f.document_id + f.name} className="border-t border-slate-100 hover:bg-slate-50/60">
                <td className="px-4 py-3">
                  <input
                    type="checkbox"
                    checked={selected.includes(f.name)}
                    onChange={() => toggle(f.name)}
                    aria-label={`Selecionar ${f.name} para a ingestão`}
                    className="h-4 w-4 rounded border-slate-300"
                  />
                </td>
                <td
                  className="px-4 py-3 font-medium text-slate-800"
                  title={`Modificado em ${dateTime(new Date(f.modified_at * 1000).toISOString())}`}
                >
                  {f.name}
                </td>
                <td className="px-4 py-3">
                  {f.provider_id ? (
                    <span className="flex items-center gap-2 text-slate-700">
                      <span
                        className="h-2.5 w-2.5 rounded-full"
                        style={{ backgroundColor: PROVIDER_COLORS[f.provider_id] || "#64748b" }}
                        aria-hidden
                      />
                      {status?.providers.find((p) => p.id === f.provider_id)?.name || f.provider_id}
                    </span>
                  ) : (
                    <span className="text-amber-600" title="Não vira evidência de nenhum provedor">
                      — não atribuído
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-right text-slate-600">{f.year ?? "—"}</td>
                <td className="px-4 py-3 text-right text-slate-600">
                  {f.size === 0 ? (
                    <span className="text-red-600" title="Arquivo vazio: a extração textual falha">
                      0 KB
                    </span>
                  ) : (
                    bytes(f.size)
                  )}
                </td>
                <td className="px-4 py-3">
                  {f.indexed ? (
                    <span className="font-medium text-emerald-600">✓ Indexado</span>
                  ) : (
                    <span
                      className="font-medium text-amber-600"
                      title="Não consta na tabela de documentos ingeridos"
                    >
                      ⚠ Pendente
                    </span>
                  )}
                </td>
                <td className="px-4 py-3 text-right text-slate-600">{f.chunks ?? "—"}</td>
                <td className="px-4 py-3 text-slate-500">
                  {f.ingested_at ? dateTime(f.ingested_at) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {status && status.providers.length > 0 && (
        <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-slate-500">Trechos por provedor:</span>
          {status.providers.map((p) => (
            <span
              key={p.id}
              title={
                p.chunks === 0
                  ? "Sem documento indexado: fica fora da comparação"
                  : `${p.chunks} trecho(s) indexado(s)`
              }
              className={
                "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium " +
                (p.chunks === 0 ? "bg-amber-50 text-amber-700" : "bg-slate-100 text-slate-700")
              }
            >
              <span
                className="h-2 w-2 rounded-full"
                style={{ backgroundColor: PROVIDER_COLORS[p.id] || "#64748b" }}
                aria-hidden
              />
              {p.name}: {p.chunks}
            </span>
          ))}
        </div>
      )}

      {result && (
        <div className="mt-4 space-y-2">
          <div
            role="status"
            className={
              "rounded-2xl border px-4 py-3 text-sm " +
              (result.chunks > 0
                ? "border-emerald-300 bg-emerald-50 text-emerald-800"
                : "border-slate-300 bg-slate-50 text-slate-700")
            }
          >
            {result.message ||
              `${result.chunks} trecho(s) de ${result.files_processed} arquivo(s) indexado(s).`}
          </div>
          {result.errors.length > 0 && (
            <div className="rounded-2xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">
              <p className="mb-1 font-semibold">
                {result.errors.length} arquivo(s) não puderam ser indexados:
              </p>
              <ul className="list-disc space-y-0.5 pl-5">
                {result.errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </div>
          )}
          {result.guardrail_events && result.guardrail_events.length > 0 && (
            <div className="rounded-2xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              <p className="mb-1 font-semibold">Eventos de guardrail nesta ingestão:</p>
              <ul className="list-disc space-y-0.5 pl-5">
                {result.guardrail_events.map((ev, i) => (
                  <li key={`${ev.rule_id}-${i}`}>
                    <code>{ev.rule_id}</code> ({ev.action}) em {ev.target || "—"}: {ev.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------

/**
 * Um envio, em duas abas.
 *
 * "Relatório" reexibe o <Report/> a partir do `response_json` gravado — é
 * literalmente a mesma tela que o gestor viu, não uma reconstrução. "Respostas"
 * mostra as linhas normalizadas do banco, que é o que sustenta a auditoria caso
 * o formato do JSON mude no futuro.
 */
function SubmissionPage({ id, onLogout }: { id: string; onLogout: () => void }) {
  const navigate = useNavigate();
  const [detail, setDetail] = useState<SubmissionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"report" | "answers">("report");
  const [confirming, setConfirming] = useState(false);

  const handleError = useCallback(
    (err: unknown) => {
      if (err instanceof AdminAuthError) onLogout();
      else setError(err instanceof Error ? err.message : String(err));
    },
    [onLogout],
  );

  useEffect(() => {
    adminSubmission(id).then(setDetail).catch(handleError);
  }, [id, handleError]);

  if (error) {
    return (
      <div>
        <Link to="/admin" className="text-sm text-blue-600 hover:underline">
          ← Voltar para a lista
        </Link>
        <div role="alert" className="mt-4 rounded-2xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex flex-col items-center py-24 text-slate-400">
        <div className="mb-3 h-8 w-8 animate-spin rounded-full border-3 border-slate-200 border-t-blue-600" />
        Carregando envio…
      </div>
    );
  }

  return (
    <div>
      <Link to="/admin" className="text-sm text-blue-600 hover:underline">
        ← Voltar para a lista
      </Link>

      <div className="mb-6 mt-3 flex flex-wrap items-start justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div>
          <h1 className="text-lg font-bold text-slate-900">{detail.respondent_email}</h1>
          <p className="mt-0.5 text-sm text-slate-500">
            {detail.respondent_role || "cargo não informado"} · {dateTime(detail.created_at)}
          </p>
          <p className="mt-1 font-mono text-[11px] text-slate-400">trace_id: {detail.id}</p>
        </div>
        <button
          onClick={() => setConfirming(true)}
          className="rounded-xl border border-slate-300 px-3.5 py-2 text-sm font-medium text-slate-600 transition hover:border-red-300 hover:bg-red-50 hover:text-red-700"
        >
          Excluir envio
        </button>
      </div>

      <div className="mb-6 flex gap-1 border-b border-slate-200" role="tablist">
        {(
          [
            ["report", "Relatório"],
            ["answers", `Respostas do questionário (${detail.answers.length})`],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={
              "-mb-px border-b-2 px-4 py-2.5 text-sm font-medium transition " +
              (tab === key
                ? "border-blue-600 text-blue-700"
                : "border-transparent text-slate-500 hover:text-slate-800")
            }
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "report" ? (
        // Com as respostas junto, esta aba fica idêntica ao que o gestor viu.
        <Report result={detail.response_json} answers={detail.answers} />
      ) : (
        <div className="space-y-6">
          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="mb-3 font-semibold text-slate-900">Comparações par-a-par (bloco D)</h2>
            <table className="w-full text-sm">
              <tbody>
                {detail.judgments.map((j, i) => (
                  <tr key={i} className="border-t border-slate-100 first:border-0">
                    <td className="py-1.5 pr-3 text-slate-500">
                      {CRITERIA_LABELS[j.criterion_a]} × {CRITERIA_LABELS[j.criterion_b]}
                    </td>
                    <td className="py-1.5 pr-3 text-slate-700">{j.choice}</td>
                    <td className="py-1.5 text-right font-mono text-slate-900">
                      {j.ratio >= 1 ? j.ratio.toFixed(0) : `1/${Math.round(1 / j.ratio)}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
            <h2 className="mb-1 font-semibold text-slate-900">Respostas, como foram enviadas</h2>
            <p className="mb-4 text-xs leading-relaxed text-slate-500">
              O enunciado é o que estava no ar no momento do envio — se o questionário for
              reescrito depois, este registro continua mostrando o que a pessoa de fato leu.
            </p>
            <ol className="space-y-3">
              {detail.answers.map((a) => (
                <li key={a.position} className="border-t border-slate-100 pt-3 first:border-0 first:pt-0">
                  <p className="text-sm text-slate-600">
                    {(a.question_text || a.question_id).replace(/\*\*/g, "")}
                  </p>
                  <p className="mt-0.5 text-sm font-medium text-slate-900">
                    {a.choice || a.text || <span className="text-slate-400">— sem resposta —</span>}
                  </p>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}

      {confirming && (
        <ConfirmDelete
          submission={detail}
          onCancel={() => setConfirming(false)}
          onDeleted={() => navigate("/admin")}
          onError={handleError}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

/** Confirmação nomeada: a exclusão é definitiva e não há lixeira no backend. */
function ConfirmDelete({
  submission,
  onCancel,
  onDeleted,
  onError,
}: {
  submission: SubmissionListItem;
  onCancel: () => void;
  onDeleted: () => void;
  onError: (err: unknown) => void;
}) {
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  async function confirm() {
    setDeleting(true);
    try {
      await adminDeleteSubmission(submission.id);
      onDeleted();
    } catch (err) {
      onError(err);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-sm"
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
      aria-label="Confirmar exclusão"
    >
      <div
        className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-2 font-bold text-slate-900">Excluir este envio?</h3>
        <p className="mb-4 text-sm leading-relaxed text-slate-600">
          A exclusão é <strong>definitiva</strong>: saem do banco as respostas, as comparações
          par-a-par e o ranking deste envio. Não há lixeira, e o registro deixa de existir para
          efeito de auditoria.
        </p>
        <div className="mb-5 rounded-xl bg-slate-50 p-3 text-sm ring-1 ring-slate-200">
          <p className="font-medium text-slate-800">{submission.respondent_email}</p>
          <p className="text-slate-500">
            {submission.respondent_role || "cargo não informado"} ·{" "}
            {dateTime(submission.created_at)}
          </p>
          <p className="mt-1 font-mono text-[11px] text-slate-400">{submission.id}</p>
        </div>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={deleting}
            className="rounded-xl border border-slate-300 px-4 py-2 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-60"
          >
            Cancelar
          </button>
          <button
            onClick={confirm}
            disabled={deleting}
            className="rounded-xl bg-red-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-red-700 disabled:opacity-60"
          >
            {deleting ? "Excluindo…" : "Excluir definitivamente"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="text-[11px] font-medium uppercase tracking-wider text-slate-400">{label}</div>
      <div className="mt-1 text-2xl font-extrabold text-slate-900">{value}</div>
      {hint && <div className="mt-0.5 text-[11px] text-slate-400">{hint}</div>}
    </div>
  );
}

function ChartCard({
  title,
  desc,
  children,
}: {
  title: string;
  desc: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <h3 className="font-semibold text-slate-900">{title}</h3>
      <p className="mb-3 mt-0.5 text-xs leading-relaxed text-slate-500">{desc}</p>
      {children}
    </div>
  );
}
