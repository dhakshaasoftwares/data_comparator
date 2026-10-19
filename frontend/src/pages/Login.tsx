import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { ErrorBox } from "../components/ui";

export default function Login({ mode }: { mode: "login" | "register" }) {
  const { user, login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [form, setForm] = useState({ email: "", password: "", name: "", organization: "" });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const from = (location.state as { from?: string } | null)?.from || "/";

  if (user) return <Navigate to={from} replace />;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") await login(form.email, form.password);
      else await register(form);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm({ ...form, [k]: e.target.value });

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16 }}>
      <div className="card" style={{ width: "min(420px, 100%)", padding: 28 }}>
        <div className="brand" style={{ padding: "0 0 8px" }}>
          <span className="brand-mark" aria-hidden>
            <svg width="16" height="16" viewBox="0 0 16 16">
              <path d="M2 4h6M2 8h12M2 12h8" stroke="white" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </span>
          Data Comparator
        </div>
        <p className="ink-2" style={{ marginTop: 0 }}>
          {mode === "login"
            ? "Validate data migrations across any source and target."
            : "Start your free 14-day trial. No card required."}
        </p>
        <form onSubmit={submit} className="stack" style={{ gap: 12 }}>
          {mode === "register" && (
            <>
              <label className="field">
                Your name
                <input value={form.name} onChange={set("name")} autoComplete="name" />
              </label>
              <label className="field">
                Company
                <input value={form.organization} onChange={set("organization")} required />
              </label>
            </>
          )}
          <label className="field">
            Work email
            <input type="email" value={form.email} onChange={set("email")} required autoComplete="email" />
          </label>
          <label className="field">
            Password
            <input
              type="password"
              value={form.password}
              onChange={set("password")}
              required
              minLength={mode === "register" ? 8 : undefined}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />
          </label>
          <ErrorBox error={error} />
          <button className="primary" disabled={busy} style={{ justifyContent: "center" }}>
            {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>
        <p className="small ink-2" style={{ marginBottom: 0 }}>
          {mode === "login" ? (
            <>
              New here? <Link to="/register">Start a free trial</Link>
              <br />
              Demo: <code>demo@example.com</code> / <code>demo1234</code>
            </>
          ) : (
            <>
              Already have an account? <Link to="/login">Sign in</Link>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
