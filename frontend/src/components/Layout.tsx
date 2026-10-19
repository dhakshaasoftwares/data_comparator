import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/jobs", label: "Comparisons" },
  { to: "/runs", label: "Run history" },
  { to: "/connections", label: "Connections" },
  { to: "/team", label: "Team" },
  { to: "/billing", label: "Plan & billing" },
];

function ThemeToggle() {
  const [theme, setTheme] = useState<string>(() => {
    try {
      return localStorage.getItem("dc_theme") || "system";
    } catch {
      return "system";
    }
  });
  useEffect(() => {
    if (theme === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("dc_theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return (
    <select aria-label="Theme" value={theme} onChange={(e) => setTheme(e.target.value)} style={{ padding: "4px 8px" }}>
      <option value="system">System theme</option>
      <option value="light">Light</option>
      <option value="dark">Dark</option>
    </select>
  );
}

export default function Layout() {
  const { user, logout } = useAuth();
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            <svg width="16" height="16" viewBox="0 0 16 16">
              <path d="M2 4h6M2 8h12M2 12h8" stroke="white" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </span>
          Data Comparator
        </div>
        <nav className="nav">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-foot stack" style={{ gap: 8 }}>
          <div>
            <div style={{ fontWeight: 600 }}>{user?.organization.name}</div>
            <div className="muted">
              {user?.email} · {user?.role}
            </div>
            <div className="muted">Plan: {user?.organization.plan}</div>
          </div>
          <ThemeToggle />
          <button className="small" onClick={logout}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
