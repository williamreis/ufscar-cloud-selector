import { useNavigate } from "react-router-dom";
import Stepper from "../components/Stepper";

const CRITERIA = [
  {
    icon: "🌱",
    title: "Sustentabilidade",
    accent: "#1baf7a",
    text: "Eficiência energética dos data centers, uso de energia renovável e metas de redução de emissões de carbono.",
  },
  {
    icon: "⚡",
    title: "Desempenho",
    accent: "#2a78d6",
    text: "Disponibilidade (uptime), latência, escalabilidade e qualidade do suporte técnico oferecido.",
  },
  {
    icon: "🔒",
    title: "Segurança",
    accent: "#4a3aa7",
    text: "Certificações (ISO 27001, SOC 2, GDPR), backup, recuperação de desastres e conformidade regulatória.",
  },
];

const STEPS = [
  { num: 1, icon: "📋", title: "Questionário", text: "Responda perguntas objetivas e dissertativas sobre suas prioridades." },
  { num: 2, icon: "⚖️", title: "IA + AHP", text: "A IA extrai pesos das respostas e o AHP calcula o ranking dos provedores." },
  { num: 3, icon: "📚", title: "RAG", text: "O sistema busca evidências reais nos relatórios oficiais de cada provedor." },
  { num: 4, icon: "📊", title: "Relatório", text: "Veja o ranking, os gráficos comparativos e as evidências da recomendação." },
];

export default function Home() {
  const navigate = useNavigate();

  return (
    <div className="animate-fade-in-up">
      <Stepper current="home" />

      <section className="text-center px-4 pt-6 pb-8">
        <span className="mb-5 inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white/70 px-3.5 py-1.5 text-xs font-medium text-slate-600 shadow-sm backdrop-blur">
          <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
          Apoio à decisão baseado em evidências
        </span>

        <h1 className="mx-auto max-w-3xl text-4xl sm:text-5xl font-extrabold leading-[1.1] tracking-tight text-slate-900">
          Qual provedor de nuvem é o{" "}
          <span className="bg-gradient-to-r from-blue-600 to-indigo-600 bg-clip-text text-transparent">
            certo para a sua instituição
          </span>
          ?
        </h1>

        <p className="mx-auto mt-5 max-w-2xl text-base sm:text-lg leading-relaxed text-slate-600">
          Uma ferramenta de apoio à decisão para gestores de TI da UFSCar, combinando{" "}
          <strong className="text-slate-800">AHP</strong> (método de decisão multicritério),{" "}
          <strong className="text-slate-800">Inteligência Artificial</strong> e{" "}
          <strong className="text-slate-800">evidências reais</strong> extraídas de relatórios
          oficiais de sustentabilidade, desempenho e segurança dos provedores.
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
            Anexar meus documentos
          </button>
        </div>
        <p className="mt-3 text-xs text-slate-400">
          Leva cerca de 5 minutos · Nenhum cadastro necessário
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
            <div
              className="mb-3 flex h-11 w-11 items-center justify-center rounded-xl text-xl"
              style={{ backgroundColor: `${c.accent}15` }}
            >
              {c.icon}
            </div>
            <h3 className="mb-1.5 font-bold text-slate-900">{c.title}</h3>
            <p className="text-sm leading-relaxed text-slate-500">{c.text}</p>
          </div>
        ))}
      </section>

      <section className="relative overflow-hidden rounded-3xl border border-slate-200/80 bg-white p-8 sm:p-10 my-10 shadow-sm">
        <div className="absolute -right-24 -top-24 h-64 w-64 rounded-full bg-blue-500/5 blur-3xl" aria-hidden />
        <div className="relative max-w-3xl">
          <span className="text-xs font-semibold uppercase tracking-wider text-blue-600">
            Metodologia
          </span>
          <h2 className="mt-2 mb-4 text-2xl font-bold text-slate-900">
            Como funciona o método AHP?
          </h2>
          <p className="mb-4 leading-relaxed text-slate-600">
            O <strong className="text-slate-800">Analytic Hierarchy Process (AHP)</strong> é um
            método de decisão multicritério que estrutura um problema complexo — como escolher entre
            AWS, Azure, Google Cloud e outros — em uma hierarquia de critérios (Sustentabilidade,
            Desempenho e Segurança) e alternativas (os provedores). Cada critério recebe um{" "}
            <strong className="text-slate-800">peso</strong> de acordo com sua importância relativa
            para o gestor, e cada provedor recebe uma <strong className="text-slate-800">nota</strong>{" "}
            em cada critério. O resultado final é um{" "}
            <strong className="text-slate-800">ranking ponderado</strong>, transparente e
            rastreável, em vez de uma escolha subjetiva.
          </p>
          <p className="leading-relaxed text-slate-600">
            Nesta ferramenta, os pesos são calculados a partir das suas respostas: as perguntas
            fechadas geram escores numéricos e as respostas dissertativas são interpretadas por um
            modelo de <strong className="text-slate-800">IA generativa</strong>, que ajusta os pesos
            e justifica as prioridades identificadas. Em seguida, um sistema de{" "}
            <strong className="text-slate-800">RAG (Retrieval-Augmented Generation)</strong> busca
            trechos reais dos relatórios oficiais — com arquivo e página citados — para sustentar a
            recomendação com evidências verificáveis.
          </p>
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
          Responda ao questionário e receba um relatório com ranking, gráficos comparativos e
          evidências extraídas dos relatórios oficiais dos provedores.
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
