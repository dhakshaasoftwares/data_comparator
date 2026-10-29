import { Link, useNavigate } from "react-router-dom";
import { api, fmtDate, fmtNum, type RunT, type Status } from "../api";
import { useAuth } from "../auth";
import { ScoreTrend, StatusStack } from "../components/charts";
import { ErrorBox, Loading, Score, StatusPill, useData } from "../components/ui";

interface DashboardData {
  kpis: {
    connections: number;
    jobs: number;
    tables_tracked: number;
    average_score: number | null;
    rows_validated: number;
    runs_total: number;
    running: number;
  };
  table_status: Record<string, number>;
  trend: { run_id: number; job_name: string; score: number; status: Status; at: string }[];
  worst_tables: {
    job_name: string;
    run_id: number;
    source_table: string;
    target_table: string;
    score: number;
    status: Status;
    top_issue: string | null;
  }[];
  recent_runs: RunT[];
  jobs: {
    id: number;
    name: string;
    score: number | null;
    status: Status | null;
    last_run_at: string | null;
    tables: number;
  }[];
}

function statusOf(score: number | null): Status | null {
  if (score === null) return null;
  return score >= 95 ? "pass" : score >= 80 ? "warn" : "fail";
}

export default function Dashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { data, error, loading } = useData(() => api<DashboardData>("/dashboard"));

  if (!data) return error ? <ErrorBox error={error} /> : <Loading />;
  const k = data.kpis;
  const empty = k.runs_total === 0;

  return (
    <div className="stack" style={{ opacity: loading ? 0.7 : 1 }}>
      <div className="page-head">
        <div>
          <h1>Migration readiness</h1>
          <p>{user?.organization.name} · latest result of every comparison</p>
        </div>
        <div className="row">
          <Link className="btn" to="/connections">
            Add connection
          </Link>
          <Link className="btn primary" to="/jobs/new">
            New comparison
          </Link>
        </div>
      </div>

      {k.runs_total === 0 && <GettingStarted k={k} />}
      {empty ? null : (
        <>
          <div className="grid cols-4">
            <div className="card stat">
              <div className="label">Average readiness score</div>
              <div className="value">
                <Score value={k.average_score} status={statusOf(k.average_score)} />
              </div>
              <div className="hint">
                <StatusPill status={statusOf(k.average_score)} />
              </div>
            </div>
            <div className="card stat">
              <div className="label">Tables tracked</div>
              <div className="value num">{fmtNum(k.tables_tracked)}</div>
              <div className="hint">across {k.jobs} comparison(s)</div>
            </div>
            <div className="card stat">
              <div className="label">Source rows validated</div>
              <div className="value num">{fmtNum(k.rows_validated)}</div>
              <div className="hint">in the latest runs</div>
            </div>
            <div className="card stat">
              <div className="label">Runs</div>
              <div className="value num">{fmtNum(k.runs_total)}</div>
              <div className="hint">{k.running ? `${k.running} in progress` : `${k.connections} connection(s)`}</div>
            </div>
          </div>

          <div className="grid cols-3">
            <div className="card span-2">
              <div className="card-head">
                <h2>Readiness score by run</h2>
                <span className="muted small">last {data.trend.length} runs</span>
              </div>
              {data.trend.length ? (
                <ScoreTrend
                  pass={95}
                  warn={80}
                  points={data.trend.map((t) => ({
                    label: `Run #${t.run_id}`,
                    sub: t.job_name,
                    value: t.score,
                    at: t.at,
                  }))}
                />
              ) : (
                <div className="muted">Run a comparison to start the trend.</div>
              )}
            </div>
            <div className="card">
              <h2>Tables by status</h2>
              <StatusStack counts={data.table_status} />
              <h3 style={{ marginTop: 22 }}>Comparisons</h3>
              <div className="stack" style={{ gap: 8 }}>
                {data.jobs.map((j) => (
                  <Link key={j.id} to={`/jobs/${j.id}`} className="spread" style={{ color: "inherit" }}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{j.name}</span>
                    <span className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
                      <Score value={j.score} status={j.status} />
                      <StatusPill status={j.status} />
                    </span>
                  </Link>
                ))}
              </div>
            </div>
          </div>

          <div className="card flush">
            <div className="card-head" style={{ padding: "16px 18px 0" }}>
              <h2>Tables needing attention</h2>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Source → target</th>
                    <th>Comparison</th>
                    <th className="r">Score</th>
                    <th>Status</th>
                    <th>Biggest issue</th>
                  </tr>
                </thead>
                <tbody>
                  {data.worst_tables.map((t) => (
                    <tr
                      key={`${t.run_id}-${t.source_table}`}
                      className="clickable"
                      onClick={() => navigate(`/runs/${t.run_id}`)}
                    >
                      <td>
                        <code>{t.source_table}</code> → <code>{t.target_table}</code>
                      </td>
                      <td className="ink-2">{t.job_name}</td>
                      <td className="r">
                        <Score value={t.score} status={t.status} />
                      </td>
                      <td>
                        <StatusPill status={t.status} />
                      </td>
                      <td className="ink-2 small">{t.top_issue || "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card flush">
            <div className="card-head" style={{ padding: "16px 18px 0" }}>
              <h2>Recent runs</h2>
              <Link to="/runs" className="small">
                All runs
              </Link>
            </div>
            <RunsTable runs={data.recent_runs} />
          </div>
        </>
      )}
    </div>
  );
}

function GettingStarted({ k }: { k: DashboardData["kpis"] }) {
  const steps = [
    {
      done: k.connections >= 1,
      title: "Connect your source system",
      text: "The platform you are migrating from: Oracle, Hive, Redshift, Teradata, files…",
      to: "/connections",
      action: "Add connection",
    },
    {
      done: k.connections >= 2,
      title: "Connect your target system",
      text: "Where the data now lives: Databricks, Snowflake, Spark Parquet on S3/ADLS…",
      to: "/connections",
      action: "Add connection",
    },
    {
      done: k.jobs >= 1,
      title: "Create a comparison",
      text: "Pick both connections; tables with the same name are paired and sensible settings applied for you.",
      to: "/jobs/new",
      action: "New comparison",
    },
    {
      done: k.runs_total >= 1,
      title: "Run it and fix what it finds",
      text: "You get a readiness score per table and plain-language findings, many with a one-click fix.",
      to: "/jobs",
      action: "Go to comparisons",
    },
  ];
  const next = steps.findIndex((s) => !s.done);
  return (
    <div className="card">
      <div className="card-head">
        <h2>Get started in 4 steps</h2>
        <span className="small muted">{steps.filter((s) => s.done).length} of 4 done</span>
      </div>
      <ol className="checklist">
        {steps.map((s, i) => (
          <li key={s.title} className={s.done ? "done" : ""}>
            <span className="num-badge">{s.done ? "✓" : i + 1}</span>
            <div>
              <div className="step-title">{s.title}</div>
              <div className="small ink-2">{s.text}</div>
            </div>
            {i === next && (
              <Link className="btn primary small" to={s.to}>
                {s.action}
              </Link>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

export function RunsTable({ runs }: { runs: RunT[] }) {
  const navigate = useNavigate();
  if (!runs.length)
    return (
      <div className="muted" style={{ padding: 18 }}>
        No runs yet.
      </div>
    );
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Run</th>
            <th>Comparison</th>
            <th>Started</th>
            <th>Tables</th>
            <th className="r">Score</th>
            <th>Result</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className="clickable" onClick={() => navigate(`/runs/${r.id}`)}>
              <td className="num">#{r.id}</td>
              <td>{r.job_name}</td>
              <td className="ink-2">{fmtDate(r.started_at || r.created_at)}</td>
              <td className="num ink-2">
                {r.summary
                  ? `${r.summary.passed}✓ ${r.summary.warned}! ${r.summary.failed + r.summary.errors}✕`
                  : r.status === "running"
                    ? `${r.progress.toFixed(0)}%`
                    : "–"}
              </td>
              <td className="r">
                <Score value={r.score} status={r.result_status} />
              </td>
              <td>
                {r.status === "completed" ? <StatusPill status={r.result_status} /> : <StatusPill status={r.status} />}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
