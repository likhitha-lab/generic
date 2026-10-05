/**
 * Router shell. Auth state lives in AuthContext (wired in main.tsx);
 * ProtectedRoute guards everything except /login and /register.
 */
import { Navigate, Route, Routes } from "react-router-dom";
import NavBar from "./components/layout/NavBar";
import ProtectedRoute from "./components/layout/ProtectedRoute";
import LoginPage from "./pages/LoginPage";
import RegisterPage from "./pages/RegisterPage";
import BuilderPage from "./pages/BuilderPage";
import HistoryPage from "./pages/HistoryPage";
import AdminPage from "./pages/AdminPage";

export default function App() {
  return (
    <div className="min-h-screen bg-bg flex flex-col">
      <NavBar />

      <main className="flex-grow flex flex-col">
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />

          <Route element={<ProtectedRoute />}>
            <Route path="/" element={<BuilderPage />} />
            <Route path="/history" element={<HistoryPage />} />
          </Route>

          <Route element={<ProtectedRoute requireAdmin />}>
            <Route path="/admin" element={<AdminPage />} />
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>

      <footer className="py-8 border-t border-white/[0.08] bg-surface">
        <div className="max-w-7xl mx-auto px-6 flex flex-col md:flex-row justify-between items-center gap-4">
          <p className="text-sm text-ink-muted">&copy; 2026 Dataflix AI Resume Management. All rights reserved.</p>
          <div className="flex gap-6 text-sm text-ink-muted">
            <a href="#" className="hover:text-brand-400 transition-colors duration-150">Privacy Policy</a>
            <a href="#" className="hover:text-brand-400 transition-colors duration-150">Terms of Service</a>
            <a href="#" className="hover:text-brand-400 transition-colors duration-150">Contact Support</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
