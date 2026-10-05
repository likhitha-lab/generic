/**
 * Admin dashboard: platform-wide stats, user list, and cross-user resume
 * search/download/delete. Every route this page calls is gated server-side
 * by require_admin (see backend/app/routers/admin.py) - this page is not
 * itself a security boundary, just a convenient UI on top of that.
 */
import { useEffect, useState } from "react";
import { Loader2, Search, ShieldCheck, Trash2, Users } from "lucide-react";
import DownloadButtons from "../components/upload/DownloadButtons";
import { getDashboardStats, listAllResumes, listAllUsers, adminDeleteResume } from "../api/admin";
import type { AdminResume, AdminUser, DashboardStats } from "../types/admin";

type AdminTab = "dashboard" | "users" | "resumes";

export default function AdminPage() {
  const [tab, setTab] = useState<AdminTab>("dashboard");

  return (
    <div className="max-w-6xl w-full mx-auto py-12 px-4">
      <h1 className="text-3xl font-bold text-ink mb-2">Admin Dashboard</h1>
      <p className="text-ink-muted mb-8">Platform-wide visibility across all users and resumes.</p>

      <div className="flex gap-2 mb-8">
        <TabButton active={tab === "dashboard"} onClick={() => setTab("dashboard")} icon={<ShieldCheck className="w-4 h-4" />} label="Overview" />
        <TabButton active={tab === "users"} onClick={() => setTab("users")} icon={<Users className="w-4 h-4" />} label="Users" />
        <TabButton active={tab === "resumes"} onClick={() => setTab("resumes")} icon={<Search className="w-4 h-4" />} label="All Resumes" />
      </div>

      {tab === "dashboard" && <DashboardTab />}
      {tab === "users" && <UsersTab />}
      {tab === "resumes" && <ResumesTab />}
    </div>
  );
}

function TabButton({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string }) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold transition-all duration-150 ${
        active ? "bg-brand-600 text-white shadow-sm shadow-brand-900/30" : "bg-transparent text-ink-muted border border-white/[0.1] hover:bg-white/[0.04] hover:border-white/[0.2]"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-surface rounded-2xl border border-white/[0.08] p-6 shadow-sm shadow-black/20">
      <p className="text-3xl font-extrabold text-ink">{value}</p>
      <p className="text-sm text-ink-muted mt-1">{label}</p>
    </div>
  );
}

function DashboardTab() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDashboardStats()
      .then(setStats)
      .catch((err) => {
        console.error(err);
        setError("Could not load dashboard stats.");
      });
  }, []);

  if (error) return <div className="p-4 bg-danger-500/10 border border-danger-500/25 rounded-lg text-sm text-danger-400">{error}</div>;
  if (!stats) return <div className="flex justify-center py-20 text-ink-muted"><Loader2 className="w-6 h-6 animate-spin" /></div>;

  return (
    <div className="space-y-8">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Users" value={stats.total_users} />
        <StatCard label="Admins" value={stats.total_admins} />
        <StatCard label="Total Resumes" value={stats.total_resumes} />
        <StatCard label="Total Versions" value={stats.total_versions} />
        <StatCard label="Resumes Today" value={stats.resumes_created_today} />
        <StatCard label="Resumes (7d)" value={stats.resumes_created_last_7_days} />
      </div>

      <div className="bg-surface rounded-2xl border border-white/[0.08] shadow-sm shadow-black/20 overflow-hidden">
        <h3 className="font-bold text-ink px-6 py-4 border-b border-white/[0.08]">Recent Activity</h3>
        <div className="divide-y divide-white/[0.08] max-h-96 overflow-auto">
          {stats.recent_actions.map((action, i) => (
            <div key={i} className="px-6 py-3 flex items-center justify-between text-sm">
              <span className="text-ink font-medium">{action.action}</span>
              <span className="text-ink-muted">
                {action.entity_type ? `${action.entity_type} #${action.entity_id}` : ""} &middot; {new Date(action.created_at).toLocaleString()}
              </span>
            </div>
          ))}
          {stats.recent_actions.length === 0 && <p className="px-6 py-4 text-sm text-ink-muted">No activity yet.</p>}
        </div>
      </div>
    </div>
  );
}

function UsersTab() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listAllUsers()
      .then(setUsers)
      .catch((err) => {
        console.error(err);
        setError("Could not load users.");
      })
      .finally(() => setIsLoading(false));
  }, []);

  if (error) return <div className="p-4 bg-danger-500/10 border border-danger-500/25 rounded-lg text-sm text-danger-400">{error}</div>;
  if (isLoading) return <div className="flex justify-center py-20 text-ink-muted"><Loader2 className="w-6 h-6 animate-spin" /></div>;

  return (
    <div className="bg-surface rounded-2xl border border-white/[0.08] shadow-sm shadow-black/20 overflow-hidden">
      <table className="w-full text-sm">
        <thead className="bg-surface-2 text-ink-muted text-left">
          <tr>
            <th className="px-6 py-3 font-medium">Name</th>
            <th className="px-6 py-3 font-medium">Email</th>
            <th className="px-6 py-3 font-medium">Role</th>
            <th className="px-6 py-3 font-medium">Resumes</th>
            <th className="px-6 py-3 font-medium">Status</th>
            <th className="px-6 py-3 font-medium">Joined</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.08]">
          {users.map((u) => (
            <tr key={u.id}>
              <td className="px-6 py-3 font-medium text-ink">{u.full_name}</td>
              <td className="px-6 py-3 text-ink-muted">{u.email}</td>
              <td className="px-6 py-3">
                <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${u.role === "admin" ? "bg-accent-500/15 text-accent-300" : "bg-white/[0.06] text-ink-muted"}`}>
                  {u.role}
                </span>
              </td>
              <td className="px-6 py-3 text-ink-muted">{u.resume_count}</td>
              <td className="px-6 py-3">
                <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${u.is_active ? "bg-success-500/15 text-success-500" : "bg-danger-500/15 text-danger-500"}`}>
                  {u.is_active ? "Active" : "Deactivated"}
                </span>
              </td>
              <td className="px-6 py-3 text-ink-muted">{new Date(u.created_at).toLocaleDateString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ResumesTab() {
  const [resumes, setResumes] = useState<AdminResume[]>([]);
  const [search, setSearch] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = async (query?: string) => {
    setIsLoading(true);
    setError(null);
    try {
      setResumes(await listAllResumes(query));
    } catch (err) {
      console.error(err);
      setError("Could not load resumes.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    load(search || undefined);
  };

  const handleDelete = async (resumeId: number) => {
    if (!window.confirm("Delete this resume for its owner? This cannot be undone.")) return;
    setBusyId(resumeId);
    try {
      await adminDeleteResume(resumeId);
      await load(search || undefined);
    } catch (err) {
      console.error(err);
      setError("Could not delete this resume.");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="space-y-6">
      <form onSubmit={handleSearch} className="flex gap-2">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by resume title or owner email..."
          className="flex-grow px-4 py-2.5 bg-white/[0.03] border border-white/[0.1] rounded-lg text-sm text-ink placeholder:text-ink-muted/60 focus:ring-2 focus:ring-brand-500 focus:border-brand-500 focus:outline-none transition-colors duration-150"
        />
        <button type="submit" className="px-5 py-2.5 bg-brand-600 text-white rounded-lg text-sm font-semibold hover:bg-brand-500 transition-colors duration-150">
          Search
        </button>
      </form>

      {error && <div className="p-4 bg-danger-500/10 border border-danger-500/25 rounded-lg text-sm text-danger-400">{error}</div>}

      {isLoading ? (
        <div className="flex justify-center py-20 text-ink-muted"><Loader2 className="w-6 h-6 animate-spin" /></div>
      ) : resumes.length === 0 ? (
        <p className="text-ink-muted">No resumes found.</p>
      ) : (
        <div className="space-y-3">
          {resumes.map((r) => (
            <div key={r.id} className="bg-surface rounded-xl border border-white/[0.08] p-5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
              <div>
                <p className="font-semibold text-ink">{r.title}</p>
                <p className="text-xs text-ink-muted">
                  {r.owner_email} &middot; {r.source_type} &middot; {r.version_count} version{r.version_count === 1 ? "" : "s"}
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                {r.latest_version && (
                  <div className="w-40">
                    <DownloadButtons resumeId={r.id} versionId={r.latest_version.id} filenameBase={r.title} asAdmin />
                  </div>
                )}
                <button
                  onClick={() => handleDelete(r.id)}
                  disabled={busyId === r.id}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-danger-500 hover:bg-danger-500/10 rounded-lg transition-colors duration-150 disabled:opacity-50"
                >
                  <Trash2 className="w-3.5 h-3.5" /> Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
