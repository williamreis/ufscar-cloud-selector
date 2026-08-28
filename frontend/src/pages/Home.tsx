import { useNavigate } from "react-router-dom";
import Stepper from "../components/Stepper";

const CRITERIA = [
  {
    icon: "🌱",
    title: "Sustentabilidade",
    accent: "#1baf7a",
    count: "5 indicadores",
    text: "Eficiência energética dos data centers (PUE), uso de fontes renováveis, redução de emissões, gestão de resíduos eletrônicos e circularidade dos equipamentos.",
  },
  {
    icon: "⚡",
    title: "Desempenho Operacional",
    accent: "#2a78d6",
    count: "4 indicadores",
    text: "Disponibilidade dos serviços, latência e tempo de resposta, escalabilidade e elasticidade, suporte técnico e resposta a incidentes.",
  },
  {
    icon: "🔒",
    title: "Segurança da Informação",
    accent: "#4a3aa7",
    count: "4 indicadores",
    text: "Certificações e conformidade, backup e continuidade de negócio, identidade e controle de acesso, proteção de dados e criptografia.",
  },
];

const ROLES = [
  {
    label: "Pesos das dimensões",
    text: "Comparações par a par informadas pelo gestor",
    kind: "Determinístico",
  },
  {
    label: "Pesos dos indicadores",
    text: "Respostas de relevância, por regra fixa",
    kind: "Determinístico",
  },
  {
    label: "Evidências",
    text: "Recuperação nos documentos dos provedores",
    kind: "Recuperação",
  },
  {
    label: "Leitura das evidências",
    text: "Modelo de linguagem extrai valor ou nível de atendimento",
    kind: "Probabilístico",
  },
  {
    label: "Notas, ponderação e ranking",
    text: "Normalização e soma ponderada em código",
    kind: "Determinístico",
  },
];

const KIND_STYLE: Record<string, string> = {
  "Determinístico": "bg-blue-50 text-blue-700 ring-blue-100",
  "Recuperação": "bg-amber-50 text-amber-700 ring-amber-100",
  "Probabilístico": "bg-violet-50 text-violet-700 ring-violet-100",
};

const STEPS = [
  {
    num: 1,
    icon: "📋",
    title: "Questionário",
    text: "25 perguntas em cinco blocos: relevância dos indicadores, comparações par a par e requisitos institucionais.",
  },
  {
    num: 2,
    icon: "⚖️",
    title: "Pesos (AHP)",
    text: "Seus julgamentos viram os pesos das dimensões, com verificação da razão de consistência antes de prosseguir.",
  },
  {
    num: 3,
    icon: "📚",
    title: "Evidências (RAG)",
    text: "Para cada provedor e indicador, o sistema recupera trechos dos documentos oficiais e extrai o dado publicado.",
  },
  {
    num: 4,
    icon: "📊",
    title: "Ranking e relatório",
    text: "Normalização, soma ponderada e ordenação em código determinístico, com memória de cálculo e fontes citadas.",
  },
];

export default function Home() {
  const navigate = useNavigate();

  return (
    <div className="animate-fade-in-up">
      <Stepper current="home" />

      <section className="text-center px-4 pt-6 pb-8">
        <span className="mb-5 inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white/70 px-3.5 py-1.5 text-xs font-medium text-slate-600 shadow-sm backdrop-blur">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
          Apoio à decisão com evidência documental rastreável
        </span>

        <h1 className="mx-auto max-w-3xl text-4xl sm:text-5xl font-extrabold leading-[1.1] tracking-tight text-slate-900">
          Qual provedor de nuvem é o{" "}
          <span className="bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
            certo para a sua instituição
          </span>
          ?
        </h1>

        <p className="mx-auto mt-5 max-w-2xl text-base sm:text-lg leading-relaxed text-slate-600">
          Ferramenta de apoio à decisão para gestores de TI. O{" "}
          <strong className="text-slate-800">AHP</strong> transforma os seus julgamentos em pesos
          para <strong className="text-slate-800">sustentabilidade</strong>,{" "}
          <strong className="text-slate-800">desempenho operacional</strong> e{" "}
          <strong className="text-slate-800">segurança da informação</strong>; o desempenho de cada
          provedor vem de <strong className="text-slate-800">evidências</strong> extraídas dos
          documentos oficiais, com arquivo e página citados. A recomendação é rastreável — a decisão
          continua sendo sua.
        </p>

        <div className="mt-8 flex flex-col sm:flex-row items-center justify-center gap-3">
          <button
            onClick={() => navigate("/questionnaire")}
            className="group flex items-center gap-2 rounded-2xl bg-gradient-to-br from-blue-600 to-indigo-700 px-8 py-4 text-base font-semibold text-white shadow-lg shadow-blue-600/25 transition-all hover:shadow-xl hover:shadow-blue-600/35 hover:brightness-110 active:scale-[0.98]"
          >
            Iniciar questionário
            <span className="transition-transform group-hover:translate-x-0.5" aria-hidden>
              →
            </span>
          </button>
          <button
            onClick={() => navigate("/ingest")}
            className="rounded-2xl border border-slate-300 bg-white/70 px-6 py-4 text-base font-medium text-slate-700 shadow-sm backdrop-blur transition hover:bg-white"
          >
            Anexar documentos (opcional)
          </button>
        </div>
        <p className="mt-3 text-xs text-slate-400">
          25 perguntas em cinco blocos · Nenhum cadastro necessário
        </p>
      </section>

      <section className="grid grid-cols-1 sm:grid-cols-3 gap-4 my-10">
        {CRITERIA.map((c) => (
          <div
            key={c.title}
            className="group relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-6 shadow-sm transition-all hover:-translate-y-1 hover:shadow-lg"
          >
            <span
              className="absolute inset-x-0 top-0 h-1"
              style={{ backgroundColor: c.accent }}
              aria-hidden
            />
            <div className="mb-4 flex items-center justify-between gap-3">
              <span
                className="flex h-11 w-11 items-center justify-center rounded-xl text-xl"
                style={{ backgroundColor: `${c.accent}15` }}
              >
                {c.icon}
              </span>
              <span
                className="rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider"
                style={{ backgroundColor: `${c.accent}12`, color: c.accent }}
              >
                {c.count}
              </span>
            </div>
            <h3 className="mb-1.5 font-bold text-slate-900">{c.title}</h3>
            <p className="text-sm leading-relaxed text-slate-500">{c.text}</p>
          </div>
        ))}
      </section>

      <section className="relative overflow-hidden rounded-3xl border border-slate-200/80 bg-white my-10 shadow-sm">
        <div className="absolute -right-24 -top-24 h-64 w-64 rounded-full bg-blue-500/5 blur-3xl" aria-hidden />
        <div className="relative grid lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <div className="p-8 sm:p-10">
            <span className="text-xs font-semibold uppercase tracking-wider text-blue-600">
              Metodologia
            </span>
            <h2 className="mt-2 mb-5 text-2xl font-bold text-slate-900">
              Como a recomendação é construída
            </h2>

            <p className="mb-4 leading-relaxed text-slate-600">
              O <strong className="text-slate-800">Analytic Hierarchy Process (AHP)</strong>{" "}
              organiza a escolha entre provedores como AWS, Google Cloud e Microsoft Azure em uma
              hierarquia de dimensões (Sustentabilidade, Desempenho Operacional e Segurança da
              Informação), indicadores e alternativas. Nas comparações par a par você informa qual
              dimensão prioriza e com que intensidade; daí saem os{" "}
              <strong className="text-slate-800">pesos das dimensões</strong> e a{" "}
              <strong className="text-slate-800">razão de consistência</strong> dos seus julgamentos
              — acima do limite aceitável a avaliação não segue, e o sistema aponta o que revisar.
            </p>

            <p className="mb-4 leading-relaxed text-slate-600">
              As perguntas de relevância definem o peso de cada indicador dentro da sua dimensão, e o
              peso global é o produto dos dois níveis. As{" "}
              <strong className="text-slate-800">
                respostas dissertativas não alteram peso algum
              </strong>
              : direcionam a busca documental e a justificativa.
            </p>

            <p className="leading-relaxed text-slate-600">
              O desempenho não é opinião da ferramenta. Para cada par provedor × indicador, o{" "}
              <strong className="text-slate-800">RAG</strong> recupera trechos dos relatórios
              oficiais e um modelo de linguagem extrai o valor publicado ou o nível de atendimento,
              com arquivo e página citados. Nota, ponderação e ordenação ficam em{" "}
              <strong className="text-slate-800">código determinístico e auditável</strong> — o
              modelo não atribui pontuação, peso nem posição.
            </p>

            <p className="mt-5 rounded-2xl border border-slate-200/80 bg-slate-50 px-4 py-3 text-sm leading-relaxed text-slate-600">
              Indicador sem evidência comparável em todos os provedores sai daquela execução, para
              todas as alternativas. Ausência de evidência nunca vira nota zero.
            </p>
          </div>

          <aside className="border-t border-slate-200/80 bg-slate-50/70 p-8 sm:p-10 lg:border-l lg:border-t-0">
            <h3 className="mb-5 text-xs font-semibold uppercase tracking-wider text-slate-400">
              Quem decide o quê
            </h3>
            <ul className="space-y-4">
              {ROLES.map((r) => (
                <li key={r.label} className="border-l-2 border-slate-200 pl-3.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-semibold text-slate-800">{r.label}</span>
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ring-1 ${KIND_STYLE[r.kind]}`}
                    >
                      {r.kind}
                    </span>
                  </div>
                  <p className="mt-0.5 text-sm leading-snug text-slate-500">{r.text}</p>
                </li>
              ))}
            </ul>
          </aside>
        </div>
      </section>

      <section className="my-12">
        <h2 className="mb-8 text-center text-sm font-semibold uppercase tracking-wider text-slate-400">
          Como funciona, passo a passo
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
          {STEPS.map((s, i) => (
            <div key={s.num} className="relative text-center">
              {i < STEPS.length - 1 && (
                <span
                  className="absolute left-1/2 top-6 hidden h-px w-full bg-gradient-to-r from-slate-200 to-transparent lg:block"
                  aria-hidden
                />
              )}
              <div className="relative mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-white text-xl shadow-md ring-1 ring-slate-200">
                {s.icon}
                <span className="absolute -right-1 -top-1 flex h-5 w-5 items-center justify-center rounded-full bg-gradient-to-br from-blue-600 to-indigo-600 text-[10px] font-bold text-white">
                  {s.num}
                </span>
              </div>
              <h4 className="mb-1 font-bold text-slate-900">{s.title}</h4>
              <p className="text-sm leading-relaxed text-slate-500">{s.text}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="my-10 flex flex-col items-center gap-4 rounded-3xl bg-gradient-to-br from-slate-900 via-slate-800 to-indigo-900 px-8 py-12 text-center text-white shadow-xl">
        <h2 className="text-2xl font-bold">Pronto para começar?</h2>
        <p className="max-w-lg text-sm text-slate-300">
          Responda ao questionário e receba um relatório com o ranking, os pesos de cada dimensão e
          indicador, a memória de cálculo e as evidências documentais que sustentam cada nota — com
          arquivo e página de origem.
        </p>
        <button
          onClick={() => navigate("/questionnaire")}
          className="mt-2 rounded-2xl bg-white px-8 py-3.5 font-semibold text-slate-900 shadow-lg transition-all hover:bg-slate-100 active:scale-[0.98]"
        >
          Iniciar questionário →
        </button>
      </section>
    </div>
  );
}
