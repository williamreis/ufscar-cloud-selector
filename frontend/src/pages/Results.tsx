import { Navigate, Link } from "react-router-dom";
import Stepper from "../components/Stepper";
import Report from "../components/Report";
import { useAppState } from "../AppContext";

/**
 * Relatório do envio que o gestor acabou de fazer.
 *
 * O conteúdo vive em <Report/> porque a área de gestão reexibe o mesmo relatório
 * a partir do registro de auditoria; aqui ficam só o passo do fluxo e as ações
 * que pertencem a quem respondeu (refazer o questionário, imprimir).
 */
export default function Results() {
  const { result, answers } = useAppState();

  // O resultado vive só na memória do React: recarregar a página ou abrir
  // /results direto pela URL cai de volta no questionário.
  if (!result) return <Navigate to="/questionnaire" replace />;

  return (
    <div>
      <Stepper current="results" />

      {/* O rascunho do questionário fica em sessionStorage, então "Revisar
          comparações" volta ao bloco D com as respostas preenchidas. */}
      <Report
        result={result}
        answers={answers ?? undefined}
        reviewHref="/questionnaire#section-comparisons"
      />

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
