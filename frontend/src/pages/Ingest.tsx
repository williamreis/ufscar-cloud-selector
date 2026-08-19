import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ingestSession, listUploaded, uploadDocuments } from "../api";
import { useAppState } from "../AppContext";
import type { UploadedFile } from "../types";

export default function Ingest() {
  const { sessionId } = useAppState();
  const [files, setFiles] = useState<File[]>([]);
  const [uploaded, setUploaded] = useState<UploadedFile[]>([]);
  const [status, setStatus] = useState<{ kind: "success" | "warning" | "error"; msg: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const refreshList = () => {
    listUploaded(sessionId)
      .then(setUploaded)
      .catch(() => setUploaded([]));
  };

  useEffect(refreshList, [sessionId]);

  async function handleSubmit() {
    if (files.length === 0) {
      setStatus({ kind: "warning", msg: "Selecione pelo menos um arquivo." });
      return;
    }
    setSubmitting(true);
    setStatus(null);
    try {
      await uploadDocuments(sessionId, files);
      const result = await ingestSession(sessionId);
      setStatus({
        kind: "success",
        msg: `Documentos enviados e indexados: ${result.chunks} trechos de ${result.files_processed} arquivo(s).`,
      });
      setFiles([]);
      refreshList();
    } catch (err) {
      setStatus({ kind: "error", msg: String(err) });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <div className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-slate-900 via-slate-800 to-indigo-900 text-white p-8 mb-6 shadow-xl">
        <div className="absolute -right-16 -top-16 h-56 w-56 rounded-full bg-blue-500/20 blur-3xl" />
        <div className="relative">
          <h1 className="text-2xl font-bold mb-2">📁 Anexar documentos extras</h1>
          <p className="text-slate-300 text-sm max-w-2xl">
            Envie, se desejar, arquivos de um provedor de Cloud Computing ou de sua infraestrutura
            local (on-premises) para consulta. Os documentos devem conter informações sobre
            Sustentabilidade, Desempenho e Segurança, sendo enviados separadamente por provedor, com
            identificação clara. O envio não é obrigatório.
          </p>
        </div>
      </div>

      <p className="text-xs text-slate-400 mb-4 font-mono">Sua sessão: {sessionId.slice(0, 16)}…</p>

      <div className="rounded-2xl border-2 border-dashed border-slate-300 bg-white/60 p-6 mb-4 text-center transition hover:border-blue-400 hover:bg-blue-50/30">
        <div className="mb-2 text-3xl" aria-hidden>
          📤
        </div>
        <input
          id="file-input"
          type="file"
          multiple
          accept=".pdf,.txt"
          onChange={(e) => setFiles(Array.from(e.target.files || []))}
          className="block w-full text-sm text-slate-600 file:mr-4 file:rounded-lg file:border-0 file:bg-slate-900 file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-slate-700"
        />
        <p className="mt-2 text-xs text-slate-400">Formatos aceitos: PDF e TXT</p>
        {files.length > 0 && (
          <p className="mt-2 text-sm font-medium text-blue-700">
            {files.length} arquivo(s) selecionado(s)
          </p>
        )}
      </div>

      <button
        onClick={handleSubmit}
        disabled={submitting}
        className="flex w-full items-center justify-center gap-2 rounded-2xl bg-gradient-to-br from-blue-600 to-indigo-700 px-6 py-3.5 font-semibold text-white shadow-lg shadow-blue-600/25 transition-all hover:shadow-xl hover:brightness-110 active:scale-[0.99] disabled:opacity-60"
      >
        {submitting && (
          <span className="h-4 w-4 rounded-full border-2 border-white/40 border-t-white animate-spin" />
        )}
        {submitting ? "Enviando e indexando…" : "Enviar e indexar documentos"}
      </button>

      {status && (
        <div
          className={
            "mt-4 rounded-lg px-4 py-3 text-sm border " +
            (status.kind === "success"
              ? "bg-green-50 border-green-300 text-green-800"
              : status.kind === "warning"
                ? "bg-amber-50 border-amber-300 text-amber-800"
                : "bg-red-50 border-red-300 text-red-700")
          }
        >
          {status.msg}
        </div>
      )}

      <h2 className="font-semibold text-slate-800 mt-8 mb-2">Listagem de arquivos anexados em sua sessão</h2>
      {uploaded.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-3 py-2 text-left">Nome</th>
                <th className="px-3 py-2 text-left">Tamanho</th>
              </tr>
            </thead>
            <tbody>
              {uploaded.map((f) => (
                <tr key={f.name} className="border-t border-slate-100">
                  <td className="px-3 py-2">{f.name}</td>
                  <td className="px-3 py-2">{f.size} bytes</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-slate-400">Nenhum arquivo anexado na sua sessão.</p>
      )}

      <Link to="/questionnaire" className="inline-block mt-6 text-blue-600 text-sm hover:underline">
        ← Voltar ao questionário
      </Link>
    </div>
  );
}
