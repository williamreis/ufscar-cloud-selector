import { useState } from "react";

/**
 * Forma mínima comum entre o que o questionário envia (AnswerPayload) e o que o
 * banco de auditoria devolve (SubmissionDetail["answers"]), para que o mesmo
 * componente sirva ao relatório recém-gerado e ao envio recuperado do banco.
 */
export interface AnsweredQuestion {
  question_id: string;
  question_text?: string | null;
  choice?: string | null;
  text?: string | null;
}

/**
 * Texto da resposta.
 *
 * As comparações do bloco D não têm `choice`: elas viajam em forma estruturada
 * (preferência + intensidade), e a frase legível equivalente é montada pelo
 * backend. `derived` traz essa frase por question_id — sem ela, as perguntas
 * 17–19 apareceriam como não respondidas nesta lista, embora tenham sido as que
 * definiram os pesos. Nos envios recuperados do banco o `choice` já vem
 * preenchido, e o mapa não é necessário.
 */
function answerText(
  a: AnsweredQuestion,
  derived?: Record<string, string>,
): string | undefined {
  return a.choice || a.text?.trim() || derived?.[a.question_id];
}

/** Remove o markdown-lite (**) e separa o número do enunciado. */
function splitLabel(label: string): { numero: string | null; texto: string } {
  const limpo = label.replace(/\*\*/g, "").trim();
  const m = limpo.match(/^(\d+)\.\s*(.*)$/s);
  return m ? { numero: m[1], texto: m[2] } : { numero: null, texto: limpo };
}

/**
 * As respostas do questionário, como foram enviadas.
 *
 * O relatório mostra o resultado; isto mostra a entrada que o produziu. As
 * dissertativas em branco aparecem marcadas como não respondidas em vez de
 * sumirem da lista — saber o que ficou vazio faz parte da leitura.
 */
export default function QuestionnaireAnswers({
  answers,
  derived,
}: {
  answers: AnsweredQuestion[];
  /** question_id → frase legível, para as respostas que não cabem em `choice` */
  derived?: Record<string, string>;
}) {
  const [open, setOpen] = useState(false);

  const respondidas = answers.filter((a) => answerText(a, derived)).length;
  const emBranco = answers.length - respondidas;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center gap-3 p-5">
        <div
          className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-slate-100 text-xl"
          aria-hidden
        >
          📝
        </div>
        <div className="min-w-[16rem] flex-1">
          <h3 className="font-bold text-slate-900">Respostas do questionário</h3>
          <p className="mt-0.5 text-sm text-slate-500">
            {respondidas} de {answers.length} perguntas respondidas
            {emBranco > 0 && ` · ${emBranco} deixada${emBranco > 1 ? "s" : ""} em branco`}
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpen(!open)}
          aria-expanded={open}
          className="rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50"
        >
          {open ? "Ocultar" : "Ver"} respostas
        </button>
      </div>

      {open && (
        <ol className="divide-y divide-slate-100 border-t border-slate-100">
          {answers.map((a, i) => {
            const { numero, texto } = splitLabel(a.question_text || a.question_id);
            const resposta = answerText(a, derived);
            return (
              <li key={`${a.question_id}-${i}`} className="flex gap-3 px-5 py-3.5">
                <span className="mt-0.5 flex h-6 min-w-6 shrink-0 items-center justify-center rounded-md bg-slate-100 px-1 text-[11px] font-bold text-slate-500">
                  {numero || "•"}
                </span>
                <div className="min-w-0">
                  <p className="text-sm leading-relaxed text-slate-600">{texto}</p>
                  {resposta ? (
                    <p className="mt-1 text-sm font-medium text-slate-900">{resposta}</p>
                  ) : (
                    <p className="mt-1 text-sm italic text-slate-400">não respondida</p>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
