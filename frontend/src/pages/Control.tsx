import { useState } from "react";
import { ingestGlobal } from "../api";
import type { IngestResult } from "../types";

// Obs.: assim como na versão anterior (Streamlit), esta senha só esconde o botão
// da UI — o endpoint /api/documents/ingest-global no backend não tem autenticação
// própria, então isto é obscuridade de interface, não controle de acesso real.
const ADMIN_PASSWORD = import.meta.env.VITE_ADMIN_PASSWORD || "admin";

export default function Control() {
  const [authenticated, setAuthenticated] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<IngestResult | null>(null);

  if (!authenticated) {
    return (
      <div className="max-w-sm">
        <h1 className="text-xl font-bold text-slate-900 mb-2">Acesso restrito</h1>
        <p className="text-sm text-slate-500 mb-4">
          Acesso ao painel de ingestão de documentos do administrador (data/pdf). Não há link no
          menu — acesse digitando <code>/control</code> na URL.
        </p>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Senha de administrador"
          className="w-full rounded-lg border border-slate-400 px-3 py-2.5 text-sm mb-3"
        />
        <button
          onClick={() => {
            if (password === ADMIN_PASSWORD) {
              setAuthenticated(true);
              setError(null);
            } else {
              setError("Senha incorreta.");
            }
          }}
          className="w-full rounded-lg bg-slate-900 text-white px-4 py-2.5 text-sm font-semibold"
        >
          Entrar
        </button>
        {error && <p className="text-red-600 text-sm mt-2">{error}</p>}
      </div>
    );
  }

  return (
    <div>
      <h1 className="text-xl font-bold text-slate-900 mb-2">Painel do administrador</h1>
      <p className="text-sm text-slate-500 mb-4">
        Ingestão dos documentos em <code>data/pdf</code>. Esses arquivos são consultados em{" "}
        <strong>todas</strong> as buscas RAG.
      </p>
      <button onClick={() => setAuthenticated(false)} className="text-sm text-slate-500 hover:underline mb-6">
        Sair do painel
      </button>

      <button
        onClick={async () => {
          setLoading(true);
          setResult(null);
          try {
            setResult(await ingestGlobal());
          } catch (e) {
            setResult({ chunks: 0, files_processed: 0, details: [], errors: [String(e)] });
          } finally {
            setLoading(false);
          }
        }}
        disabled={loading}
        className="w-full rounded-xl bg-gradient-to-br from-blue-600 to-blue-700 px-6 py-3 font-semibold text-white disabled:opacity-60"
      >
        {loading ? "Indexando documentos de data/pdf…" : "Ingestão global — indexar documentos em data/pdf"}
      </button>

      {result && (
        <div className="mt-4 space-y-2">
          <div className="rounded-lg bg-green-50 border border-green-300 px-4 py-3 text-sm text-green-800">
            {result.chunks} trechos de {result.files_processed} arquivo(s) indexados.
          </div>
          {result.errors.length > 0 && (
            <div className="rounded-lg bg-amber-50 border border-amber-300 px-4 py-3 text-sm text-amber-800">
              {result.errors.slice(0, 3).join("; ")}
            </div>
          )}
        </div>
      )}

      <p className="text-xs text-slate-400 mt-4">
        Coloque os PDF/TXT em data/pdf no servidor e clique acima para indexar.
      </p>
    </div>
  );
}
