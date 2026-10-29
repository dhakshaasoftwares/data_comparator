import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import Layout from "./components/Layout";
import { Loading, ToastProvider } from "./components/ui";
import Billing from "./pages/Billing";
import Connections from "./pages/Connections";
import Dashboard from "./pages/Dashboard";
import JobEditor from "./pages/JobEditor";
import Jobs from "./pages/Jobs";
import Login from "./pages/Login";
import RunDetail from "./pages/RunDetail";
import Runs from "./pages/Runs";
import Team from "./pages/Team";
import "./styles.css";

try {
  const theme = localStorage.getItem("dc_theme");
  if (theme && theme !== "system") document.documentElement.setAttribute("data-theme", theme);
} catch {
  /* ignore */
}

function RequireAuth({ children }: { children: JSX.Element }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Loading />;
  if (!user) return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  return children;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthProvider>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login mode="login" />} />
            <Route path="/register" element={<Login mode="register" />} />
            <Route
              element={
                <RequireAuth>
                  <Layout />
                </RequireAuth>
              }
            >
              <Route index element={<Dashboard />} />
              <Route path="jobs" element={<Jobs />} />
              <Route path="jobs/new" element={<JobEditor />} />
              <Route path="jobs/:id" element={<JobEditor />} />
              <Route path="runs" element={<Runs />} />
              <Route path="runs/:id" element={<RunDetail />} />
              <Route path="connections" element={<Connections />} />
              <Route path="team" element={<Team />} />
              <Route path="billing" element={<Billing />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </AuthProvider>
  </StrictMode>,
);
