import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Stepper from "../components/Stepper";
import LoadingOverlay from "../components/LoadingOverlay";
import { loadQuestions, postRecommend } from "../api";
import { useAppState } from "../AppContext";
import type { AnswerPayload, QuestionDef, QuestionsFile } from "../types";

/** Extrai o "1." de "**1.** Ao definir critérios…" para referenciar a pergunta nos erros. */
function questionNumber(label: string): string {
  const m = label.match(/\*\*(\d+)\.\*\*/) || label.match(/^(\d+)\./);
  return m ? m[1] : "";
}

/** Texto da pergunta sem o markdown e sem o número, para listar nos erros. */
function questionText(label: string): string {
  return label.replace(/\*\*/g, "").replace(/^\s*\d+\.\s*/, "").trim();
}

export default function Questionnaire() {
  const navigate = useNavigate();
  const { sessionId, setResult } = useAppState();

  const [questions, setQuestions] = useState<QuestionsFile | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [respondent, setRespondent] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [missing, setMissing] = useState<QuestionDef[]>([]);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const errorBoxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    loadQuestions()
      .then(setQuestions)
      .catch((e) => setLoadError(String(e)));
  }, []);

  const allQuestions = useMemo(
    () => questions?.sections.flatMap((s) => s.questions) ?? [],
    [questions],
  );
  const requiredQuestions = useMemo(
    () => allQuestions.filter((q) => q.required && q.type === "choice"),
    [allQuestions],
  );
  const answeredCount = requiredQuestions.filter((q) => values[q.id]).length;
  const progress = requiredQuestions.length
    ? Math.round((answeredCount / requiredQuestions.length) * 100)
    : 0;

  const missingIds = useMemo(() => new Set(missing.map((q) => q.id)), [missing]);

  if (loadError) {
    return (
      <div className="rounded-xl bg-red-50 border border-red-200 p-6 text-red-700">
        Não foi possível carregar o questionário: {loadError}
      </div>
    );
  }

  if (!questions) {
    return (
      <div className="flex flex-col items-center py-24 text-slate-400">
        <div className="h-8 w-8 rounded-full border-3 border-slate-200 border-t-blue-600 animate-spin mb-3" />
        Carregando questionário…
      </div>
    );
  }

  function setValue(id: string, val: string) {
    setValues((prev) => ({ ...prev, [id]: val }));
    // Some do painel de erros assim que o campo é respondido.
    setMissing((prev) => (val ? prev.filter((q) => q.id !== id) : prev));
  }

  function focusQuestion(id: string) {
    const el = document.getElementById(`field-${id}`);
    el?.scrollIntoView({ behavior: "smooth", block: "center" });
    (el as HTMLSelectElement | null)?.focus({ preventScroll: true });
  }

  const optionsFor = (opts: string | string[]): string[] =>
    Array.isArray(opts) ? opts : questions!.option_sets[opts] || [];

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitError(null);

    const notAnswered = requiredQuestions.filter((q) => !values[q.id]);
    setMissing(notAnswered);

    if (notAnswered.length > 0) {
      // Leva o usuário até a lista de pendências em vez de só mostrar um aviso genérico.
      requestAnimationFrame(() =>
        errorBoxRef.current?.scrollIntoView({ behavior: "smooth", block: "center" }),
      );
      return;
    }

    // question_text acompanha a resposta para que o backend possa mostrar ao LLM
    // o que foi perguntado — antes ele recebia só o question_id, um rótulo opaco.
    const answers: AnswerPayload[] = allQuestions.map((q) => {
      const val = values[q.id];
      return q.type === "choice"
        ? { question_id: q.id, question_text: q.label, choice: val || null, text: null }
        : { question_id: q.id, question_text: q.label, choice: null, text: val || null };
    });

    setSubmitting(true);
    try {
      const result = await postRecommend({ respondent, answers, session_id: sessionId });
      setResult(result);
      navigate("/results");
    } catch (err) {
      setSubmitError(String(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <LoadingOverlay open={submitting} />
      <Stepper current="questionnaire" />

      <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-slate-900 via-slate-800 to-indigo-900 text-white p-8 mb-6 shadow-xl">
        <div className="absolute -right-16 -top-16 h-56 w-56 rounded-full bg-blue-500/20 blur-3xl" />
        <div className="relative">
          <h1 className="text-2xl font-bold mb-2">Questionário de Seleção de Provedores</h1>
          <p className="text-slate-300 text-sm max-w-2xl">
            Responda com base em sustentabilidade, desempenho e segurança. Perguntas de múltipla
            escolha alimentam a análise AHP; os campos de texto livre são interpretados por IA para
            ajustar as prioridades.
          </p>
        </div>
      </div>

      {/* Progresso das obrigatórias — deixa claro quanto falta antes de tentar enviar */}
      <div className="sticky top-[57px] z-10 -mx-4 mb-6 border-b border-slate-200/70 bg-white/85 px-4 py-3 backdrop-blur">
        <div className="flex items-center justify-between text-xs font-medium text-slate-600 mb-1.5">
          <span>Perguntas obrigatórias respondidas</span>
          <span className={answeredCount === requiredQuestions.length ? "text-green-600" : ""}>
            {answeredCount} de {requiredQuestions.length}
          </span>
        </div>
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
          <div
            className="h-full rounded-full bg-gradient-to-r from-blue-500 to-indigo-500 transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      <form onSubmit={handleSubmit} noValidate className="space-y-8">
        <div>
          <label htmlFor="respondent" className="block text-sm font-semibold text-slate-800 mb-1.5">
            Nome do gestor ou e-mail
          </label>
          <input
            id="respondent"
            type="text"
            value={respondent}
            onChange={(e) => setRespondent(e.target.value)}
            placeholder="Opcional"
            className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm shadow-sm transition focus:border-blue-500 focus:outline-none focus:ring-4 focus:ring-blue-500/10"
          />
        </div>

        {questions.sections.map((section) => (
          <section key={section.id}>
            <div className="mb-4 flex items-center gap-2.5">
              <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br from-blue-600 to-indigo-600 text-xs font-bold text-white shadow-sm">
                {section.badge}
              </span>
              <h2 className="font-semibold text-slate-900">{section.title}</h2>
              <span className="h-px flex-1 bg-gradient-to-r from-slate-200 to-transparent" />
            </div>

            <div className="space-y-4">
              {section.questions.map((q) => {
                const isMissing = missingIds.has(q.id);
                return (
                  <div
                    key={q.id}
                    className={
                      "rounded-2xl border bg-white p-4 shadow-sm transition-all " +
                      (isMissing
                        ? "border-red-300 ring-4 ring-red-500/10"
                        : "border-slate-200/80 hover:border-slate-300")
                    }
                  >
                    <label
                      htmlFor={`field-${q.id}`}
                      className="mb-2 block text-sm leading-relaxed text-slate-700"
                    >
                      {renderLabel(q.label)}
                      {q.required && <span className="ml-1 text-red-500">*</span>}
                    </label>

                    {q.type === "choice" ? (
                      <select
                        id={`field-${q.id}`}
                        value={values[q.id] || ""}
                        onChange={(e) => setValue(q.id, e.target.value)}
                        aria-invalid={isMissing}
                        className={
                          "w-full rounded-xl border bg-white px-3.5 py-2.5 text-sm shadow-sm transition focus:outline-none focus:ring-4 " +
                          (isMissing
                            ? "border-red-400 focus:border-red-500 focus:ring-red-500/10"
                            : "border-slate-300 focus:border-blue-500 focus:ring-blue-500/10")
                        }
                      >
                        <option value="">Selecione uma opção</option>
                        {optionsFor(q.options!).map((opt) => (
                          <option key={opt} value={opt}>
                            {opt}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <textarea
                        id={`field-${q.id}`}
                        value={values[q.id] || ""}
                        onChange={(e) => setValue(q.id, e.target.value)}
                        placeholder={q.placeholder || "Opcional"}
                        rows={2}
                        className="w-full resize-y rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm shadow-sm transition focus:border-blue-500 focus:outline-none focus:ring-4 focus:ring-blue-500/10"
                      />
                    )}

                    {isMissing && (
                      <p className="mt-2 flex items-center gap-1.5 text-xs font-medium text-red-600">
                        <span aria-hidden>⚠</span> Esta pergunta é obrigatória.
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        ))}

        {/* Lista explícita do que ficou faltando, com atalho para cada pergunta */}
        {missing.length > 0 && (
          <div
            ref={errorBoxRef}
            role="alert"
            className="rounded-2xl border border-red-200 bg-red-50 p-5"
          >
            <h3 className="mb-1 flex items-center gap-2 font-semibold text-red-800">
              <span aria-hidden>⚠</span>
              {missing.length === 1
                ? "1 pergunta obrigatória não foi respondida"
                : `${missing.length} perguntas obrigatórias não foram respondidas`}
            </h3>
            <p className="mb-3 text-sm text-red-700">
              Clique em uma delas para ir direto ao campo:
            </p>
            <ul className="space-y-1.5">
              {missing.map((q) => (
                <li key={q.id}>
                  <button
                    type="button"
                    onClick={() => focusQuestion(q.id)}
                    className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left text-sm text-red-800 transition hover:bg-red-100"
                  >
                    <span className="mt-0.5 flex h-5 min-w-5 items-center justify-center rounded bg-red-200 px-1 text-[11px] font-bold text-red-900">
                      {questionNumber(q.label) || "•"}
                    </span>
                    <span className="underline decoration-red-300 underline-offset-2">
                      {questionText(q.label)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {submitError && (
          <div role="alert" className="rounded-2xl border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-700">
            Não foi possível gerar a recomendação: {submitError}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="flex w-full items-center justify-center gap-2 rounded-2xl bg-gradient-to-br from-blue-600 to-indigo-700 px-6 py-4 font-semibold text-white shadow-lg shadow-blue-600/25 transition-all hover:shadow-xl hover:shadow-blue-600/35 hover:brightness-110 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-60"
        >
          {submitting ? (
            <>
              <span className="h-4 w-4 rounded-full border-2 border-white/40 border-t-white animate-spin" />
              Processando…
            </>
          ) : (
            "Enviar questionário e obter recomendação"
          )}
        </button>
      </form>
    </div>
  );
}

// Suporte simples a **negrito** vindo do questions.json (markdown-lite)
function renderLabel(label: string) {
  const parts = label.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={i} className="font-semibold text-slate-900">
        {part.slice(2, -2)}
      </strong>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}
