import { documentUrl } from "../api";
import type { EvidenceItem } from "../types";

const CRITERION_STYLE: Record<string, { label: string; icon: string; chip: string }> = {
  sustainability: {
    label: "Sustentabilidade",
    icon: "🌱",
    chip: "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
  },
  performance: {
    label: "Desempenho",
    icon: "⚡",
    chip: "bg-blue-50 text-blue-700 ring-blue-600/20",
  },
  security: {
    label: "Segurança",
    icon: "🔒",
    chip: "bg-violet-50 text-violet-700 ring-violet-600/20",
  },
};

/**
 * Converte distância L2 do FAISS (menor = mais similar) em um rótulo legível.
 * Não é probabilidade — é só uma leitura qualitativa da proximidade.
 */
function relevanceLabel(score: number): { text: string; tone: string } {
  if (score < 0.9) return { text: "Alta", tone: "text-emerald-600" };
  if (score < 1.3) return { text: "Média", tone: "text-amber-600" };
  return { text: "Baixa", tone: "text-slate-400" };
}

export default function EvidenceCard({ ev }: { ev: EvidenceItem }) {
  const crit = ev.criterion ? CRITERION_STYLE[ev.criterion] : undefined;
  const url = documentUrl(ev);
  const rel = relevanceLabel(ev.score);
  const pageText = ev.page_label || (ev.page ? String(ev.page) : null);

  return (
    <article className="group rounded-xl border border-slate-200 bg-white p-4 transition-all hover:border-slate-300 hover:shadow-sm">
      <header className="mb-2.5 flex flex-wrap items-center gap-2">
        {crit && (
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${crit.chip}`}
          >
            <span aria-hidden>{crit.icon}</span>
            {crit.label}
          </span>
        )}
        {ev.scope === "session" && (
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-medium text-slate-600 ring-1 ring-inset ring-slate-600/20">
            Documento da sua sessão
          </span>
        )}
        <span className="ml-auto text-[11px] text-slate-400">
          Relevância <span className={`font-semibold ${rel.tone}`}>{rel.text}</span>
        </span>
      </header>

      <blockquote className="border-l-2 border-slate-200 pl-3 text-sm leading-relaxed text-slate-600">
        {ev.page_content}
      </blockquote>

      <footer className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        {ev.file_name ? (
          url ? (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 font-medium text-blue-600 hover:text-blue-800 hover:underline"
            >
              <span aria-hidden>📄</span>
              <span className="max-w-[22rem] truncate">{ev.file_name}</span>
              {pageText && <span className="text-slate-500">· pág. {pageText}</span>}
              <span aria-hidden className="opacity-0 transition group-hover:opacity-100">
                ↗
              </span>
            </a>
          ) : (
            <span className="inline-flex items-center gap-1.5 text-slate-500">
              <span aria-hidden>📄</span>
              {ev.file_name}
              {pageText && <span>· pág. {pageText}</span>}
            </span>
          )
        ) : (
          <span className="text-slate-400">Origem não identificada</span>
        )}

        {ev.total_pages && (
          <span className="text-slate-400">de {ev.total_pages} páginas</span>
        )}
      </footer>
    </article>
  );
}
