import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { Button } from "../components/ui/Button";
import dataflixLogo from "../assets/dataflix-logo.png";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      await login(email, password);
      navigate("/");
    } catch {
      setError("Invalid email or password.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg px-4">
      <form onSubmit={handleSubmit} className="w-full max-w-sm bg-surface p-8 rounded-2xl shadow-xl shadow-black/30 border border-white/[0.08] space-y-5">
        <div className="flex flex-col items-center gap-3 mb-2">
          <img src={dataflixLogo} alt="Dataflix" className="h-9 w-auto" />
          <h1 className="text-xl font-semibold text-ink text-center tracking-tight">Sign in</h1>
        </div>

        <div>
          <label className="block text-sm font-medium text-ink-muted mb-1.5">Email</label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full px-4 py-2.5 bg-white/[0.03] border border-white/[0.1] rounded-lg text-sm text-ink focus:ring-2 focus:ring-brand-500 focus:border-brand-500 focus:outline-none transition-colors duration-150"
          />
        </div>

        <div>
          <label className="block text-sm font-medium text-ink-muted mb-1.5">Password</label>
          <input
            type="password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full px-4 py-2.5 bg-white/[0.03] border border-white/[0.1] rounded-lg text-sm text-ink focus:ring-2 focus:ring-brand-500 focus:border-brand-500 focus:outline-none transition-colors duration-150"
          />
        </div>

        {error && <p className="text-sm text-danger-500">{error}</p>}

        <Button type="submit" disabled={isSubmitting} className="w-full py-3">
          {isSubmitting ? "Signing in..." : "Sign in"}
        </Button>

        <p className="text-sm text-center text-ink-muted">
          Don&apos;t have an account?{" "}
          <Link to="/register" className="text-brand-400 font-medium hover:underline">
            Register
          </Link>
        </p>
      </form>
    </div>
  );
}
