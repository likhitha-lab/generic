import { FileJson, LayoutDashboard, LogOut, PlusCircle } from "lucide-react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import dataflixLogo from "../../assets/dataflix-logo.png";

export default function NavBar() {
  const { user, isAdmin, logout } = useAuth();

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium transition-colors duration-150 ${
      isActive ? "bg-brand-500/15 text-brand-400" : "text-ink-muted hover:bg-white/[0.06] hover:text-ink"
    }`;

  return (
    <nav className="bg-surface border-b border-white/[0.08] px-6 py-4 sticky top-0 z-10">
      <div className="max-w-7xl mx-auto flex justify-between items-center">
        <div className="flex items-center gap-8">
          <NavLink to="/" className="flex items-center gap-3">
            <img src={dataflixLogo} alt="Dataflix" className="h-8 w-auto" />
            <span className="hidden sm:block h-6 w-px bg-white/[0.12]" aria-hidden="true" />
            <span className="hidden sm:block text-lg font-semibold text-ink tracking-tight">
              AI Resume Management
            </span>
          </NavLink>

          {user && (
            <div className="hidden sm:flex items-center gap-1">
              <NavLink to="/" end className={linkClass}>
                <PlusCircle className="w-4 h-4" /> Build
              </NavLink>
              <NavLink to="/history" className={linkClass}>
                <FileJson className="w-4 h-4" /> History
              </NavLink>
              {isAdmin && (
                <NavLink to="/admin" className={linkClass}>
                  <LayoutDashboard className="w-4 h-4" /> Admin
                </NavLink>
              )}
            </div>
          )}
        </div>

        {user && (
          <div className="flex items-center gap-4">
            <span className="text-sm text-ink-muted hidden sm:inline">{user.full_name}</span>
            <button
              onClick={logout}
              className="flex items-center gap-1.5 px-3 py-2 text-sm font-medium text-ink-muted hover:text-danger-500 transition-colors duration-150"
            >
              <LogOut className="w-4 h-4" /> Logout
            </button>
          </div>
        )}
      </div>
    </nav>
  );
}
