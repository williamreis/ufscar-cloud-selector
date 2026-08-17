import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { adminLogin, adminSessionValid, ingestGlobal, setAdminToken } from "../api";
import type { IngestResult } from "../types";

export default function Control() {
  // A senha agora é conferida no servidor e o endpoint de ingestão exige o token
  // — antes a comparação era no bundle do navegador e a rota ficava aberta.
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<IngestResult | null>(null);

  useEffect(() => {
    adminSessionValid().then(setAuthenticated);
  }, []);

  async function login(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await adminLogin(password);
      setPassword("");
      setAuthenticated(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (authenticated === null) {
    return <p className="text-sm text-slate-400">Verificando sessão…</p>;
  }

  if (!authenticated) {
    return (
      <form onSubmit={login} className="max-w-sm">
        <h1 className="text-xl font-bold text-slate-900 mb-2">Acesso restrito</h1>
        <p className="text-sm text-slate-500 mb-4">
          Painel de ingestão dos documentos do administrador (data/pdf). Usa a mesma senha da{" "}
          <Link to="/admin" className="underline">
            área de gestão
          </Link>
          , definida em <code>ADMIN_PASSWORD</code> no backend.
        </p>
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Senha de administrador"
          className="w-full rounded-lg border border-slate-400 px-3 py-2.5 text-sm mb-3"
        />
        <button
          type="submit"
          disabled={!password}
          className="w-full rounded-lg bg-slate-900 text-white px-4 py-2.5 text-sm font-semibold disabled:opacity-60"
        >
          Entrar
        </button>
        {error && <p className="text-red-600 text-sm mt-2">{error}</p>}
      </form>
    );
  }

  return (
    <div>
      <h1 className="text-xl font-bold text-slate-900 mb-2">Painel do administrador</h1>
      <p className="text-sm text-slate-500 mb-4">
        Ingestão dos documentos em <code>data/pdf</code>. Esses arquivos são consultados em{" "}
        <strong>todas</strong> as buscas RAG.
      </p>
      <div className="mb-6 flex gap-4 text-sm">
        <Link to="/admin" className="text-blue-600 hover:underline">
          Ir para a área de gestão →
        </Link>
        <button
          onClick={() => {
            setAdminToken(null);
            setAuthenticated(false);
          }}
          className="text-slate-500 hover:underline"
        >
          Sair do painel
        </button>
      </div>

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
