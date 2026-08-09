import { Route, Routes } from "react-router-dom";
import { AppStateProvider } from "./AppContext";
import NavBar from "./components/NavBar";
import Home from "./pages/Home";
import Questionnaire from "./pages/Questionnaire";
import Results from "./pages/Results";
import Ingest from "./pages/Ingest";
import Control from "./pages/Control";

export default function App() {
  return (
    <AppStateProvider>
      <div className="min-h-screen flex flex-col">
        <NavBar />
        <main className="flex-1 mx-auto w-full max-w-6xl px-4 py-8">
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/questionnaire" element={<Questionnaire />} />
            <Route path="/results" element={<Results />} />
            <Route path="/ingest" element={<Ingest />} />
            <Route path="/control" element={<Control />} />
          </Routes>
        </main>
        <footer className="text-center text-xs text-slate-400 py-6">
          UFSCar - Assistente de Seleção de Provedores de Cloud
        </footer>
      </div>
    </AppStateProvider>
  );
}
