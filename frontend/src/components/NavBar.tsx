import { NavLink } from "react-router-dom";
import { useAppState } from "../AppContext";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  "relative px-3 py-2 rounded-lg text-sm font-medium transition-colors " +
  (isActive
    ? "text-blue-700 bg-blue-50"
    : "text-slate-600 hover:text-slate-900 hover:bg-slate-100");

export default function NavBar() {
  const { sessionId, result } = useAppState();

  return (
    <header className="sticky top-0 z-30 border-b border-slate-200/70 bg-white/80 backdrop-blur-md">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-2.5">
        <NavLink to="/" className="flex items-center gap-2.5 group">
          <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-blue-600 to-indigo-700 text-sm shadow-sm transition-transform group-hover:scale-105">
            ☁️
          </span>
          <span className="hidden sm:block leading-tight">
            <span className="block text-sm font-bold text-slate-900">Cloud Selector</span>
            <span className="block text-[10px] uppercase tracking-wider text-slate-400">UFSCar</span>
          </span>
        </NavLink>

        <nav className="flex items-center gap-1">
          <NavLink to="/" end className={linkClass}>
            Início
          </NavLink>
          <NavLink to="/questionnaire" className={linkClass}>
            Questionário
          </NavLink>
          <NavLink to="/ingest" className={linkClass}>
            <span className="hidden sm:inline">Anexar documentos</span>
            <span className="sm:hidden">Documentos</span>
          </NavLink>
          {result && (
            <NavLink to="/results" className={linkClass}>
              Relatório
            </NavLink>
          )}
        </nav>

        <span
          className="hidden md:inline font-mono text-[11px] text-slate-400"
          title="Identificador desta sessão (usado para associar seus documentos)"
        >
          {sessionId.slice(0, 8)}…
        </span>
      </div>
    </header>
  );
}
