import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, fmtNum } from "../api";
import { useAuth } from "../auth";
import { ErrorBox, Loading, useData } from "../components/ui";

interface Plan {
  id: string;
  name: string;
  price_month_usd: number | null;
  max_users: number | null;
  max_connections: number | null;
  max_jobs: number | null;
  max_tables_per_job: number | null;
  max_rows_per_table: number | null;
  runs_per_month: number | null;
  quality_rules: boolean;
  api_access: boolean;
  self_hosted_agent: boolean;
  sso: boolean;
  support: string;
  tagline: string;
}

interface BillingData {
  plan: Plan;
  subscription_status: string;
  billing_enabled: boolean;
  checkout_available: boolean;
  plans: Plan[];
  usage: { period: string; runs: number; rows_compared: number; connections: number; jobs: number; users: number };
}

const lim = (n: number | null) => (n === null ? "Unlimited" : fmtNum(n));

function Usage({ label, used, limit }: { label: string; used: number; limit: number | null }) {
  const pct = limit ? Math.min(100, (used / limit) * 100) : 0;
  return (
    <div>
      <div className="spread small">
        <span className="ink-2">{label}</span>
        <span className="num">
          {fmtNum(used)} / {lim(limit)}
        </span>
      </div>
      <div className="progress" style={{ marginTop: 6 }}>
        <div style={{ width: `${limit ? pct : 0}%`, background: pct >= 90 ? "var(--critical)" : undefined }} />
      </div>
    </div>
  );
}

export default function Billing() {
  const { user, refresh } = useAuth();
  const [params] = useSearchParams();
  const data = useData(() => api<BillingData>("/billing"));
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const isAdmin = user?.role === "admin";

  const choose = async (plan: Plan) => {
    setBusy(plan.id);
    setError(null);
    try {
      if (data.data!.billing_enabled) {
        const { url } = await api<{ url: string }>("/billing/checkout", { body: { plan: plan.id } });
        window.location.assign(url);
        return;
      }
      await api("/billing/plan", { body: { plan: plan.id } });
      await Promise.all([data.reload(), refresh()]);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  };

  if (!data.data) return data.error ? <ErrorBox error={data.error} /> : <Loading />;
  const d = data.data;
  const p = d.plan;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Plan & billing</h1>
          <p>
            Current plan: <b>{p.name}</b> · {d.subscription_status}
          </p>
        </div>
      </div>
      {params.get("checkout") === "success" && <div className="alert ok">Thank you! Your subscription is active.</div>}
      {!d.billing_enabled && (
        <div className="alert warn">
          Evaluation mode: payments are switched off, so you can switch plans freely to test the limits. Set{" "}
          <code>BILLING_ENABLED=true</code> and the Stripe keys to take real subscriptions.
        </div>
      )}
      <ErrorBox error={error} />

      <div className="card">
        <h2>Usage this month ({d.usage.period})</h2>
        <div className="grid cols-4">
          <Usage label="Comparison runs" used={d.usage.runs} limit={p.runs_per_month} />
          <Usage label="Connections" used={d.usage.connections} limit={p.max_connections} />
          <Usage label="Comparisons" used={d.usage.jobs} limit={p.max_jobs} />
          <Usage label="Users" used={d.usage.users} limit={p.max_users} />
        </div>
        <p className="small ink-2" style={{ marginBottom: 0 }}>
          Rows compared this month: <b className="num">{fmtNum(d.usage.rows_compared)}</b>
        </p>
      </div>

      <div className="grid cols-4">
        {d.plans.map((plan) => {
          const current = plan.id === p.id;
          return (
            <div key={plan.id} className="card stack" style={{ gap: 10, borderColor: current ? "var(--accent)" : undefined }}>
              <div>
                <h2 style={{ marginBottom: 2 }}>{plan.name}</h2>
                <div className="muted small">{plan.tagline}</div>
              </div>
              <div style={{ fontSize: 26, fontWeight: 680 }}>
                {plan.price_month_usd === null ? "Custom" : `$${fmtNum(plan.price_month_usd)}`}
                {plan.price_month_usd ? <span className="muted small"> /month</span> : null}
              </div>
              <ul className="small ink-2" style={{ paddingLeft: 18, margin: 0, lineHeight: 1.8 }}>
                <li>{lim(plan.max_users)} users</li>
                <li>{lim(plan.max_connections)} connections</li>
                <li>{lim(plan.max_tables_per_job)} tables per comparison</li>
                <li>{lim(plan.max_rows_per_table)} rows per table (row-level diff)</li>
                <li>{lim(plan.runs_per_month)} runs / month</li>
                <li>{plan.quality_rules ? "Data quality rules" : "No data quality rules"}</li>
                {plan.api_access && <li>REST API & CI/CD gating</li>}
                {plan.self_hosted_agent && <li>Self-hosted execution agent</li>}
                {plan.sso && <li>SSO</li>}
                <li>{plan.support} support</li>
              </ul>
              <div style={{ marginTop: "auto" }}>
                {current ? (
                  <span className="pill pass">✓ Current plan</span>
                ) : plan.price_month_usd === null && d.billing_enabled ? (
                  <a className="btn" href="mailto:sales@example.com">
                    Contact sales
                  </a>
                ) : (
                  isAdmin && (
                    <button className={plan.id === "team" ? "primary" : ""} onClick={() => choose(plan)} disabled={busy !== null}>
                      {busy === plan.id ? "Please wait…" : d.billing_enabled ? "Subscribe" : `Switch to ${plan.name}`}
                    </button>
                  )
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
