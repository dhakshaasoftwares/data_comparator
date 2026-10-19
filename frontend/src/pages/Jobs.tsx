import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, fmtDate, type ConnectionT, type JobT } from "../api";
import { useAuth } from "../auth";
import { Empty, ErrorBox, Loading, Score, StatusPill, useData } from "../components/ui";

export default function Jobs() {
  const { canEdit } = useAuth();
  const navigate = useNavigate();
  const jobs = useData(() => api<JobT[]>("/jobs"));
  const conns = useData(() => api<ConnectionT[]>("/connections"));
  const [error, setError] = useState<unknown>(null);
  const [starting, setStarting] = useState<number | null>(null);

  const connName = (id: number) => conns.data?.find((c) => c.id === id)?.name || `#${id}`;

  const run = async (job: JobT) => {
    setStarting(job.id);
    try {
      const r = await api(`/jobs/${job.id}/run`, { method: "POST" });
      navigate(`/runs/${r.id}`);
    } catch (e) {
      setError(e);
      setStarting(null);
    }
  };

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Comparisons</h1>
          <p>Each comparison checks one or more tables between a source and a target connection.</p>
        </div>
        {canEdit && (
          <Link className="btn primary" to="/jobs/new">
            New comparison
          </Link>
        )}
      </div>
      <ErrorBox error={error || jobs.error} />
      <div className="card flush">
        {!jobs.data ? (
          <Loading />
        ) : jobs.data.length === 0 ? (
          <Empty title="No comparisons yet">
            <p>Create one to compare schemas, row counts, values and data quality.</p>
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Source → target</th>
                  <th className="r">Tables</th>
                  <th>Mode</th>
                  <th>Last run</th>
                  <th className="r">Score</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {jobs.data.map((j) => (
                  <tr key={j.id}>
                    <td>
                      <Link to={`/jobs/${j.id}`} style={{ fontWeight: 580 }}>
                        {j.name}
                      </Link>
                      {j.description && <div className="muted small">{j.description}</div>}
                    </td>
                    <td className="ink-2">
                      {connName(j.config.source_connection_id)} → {connName(j.config.target_connection_id)}
                    </td>
                    <td className="r num">{j.table_count}</td>
                    <td className="ink-2">{j.config.options.mode}</td>
                    <td>
                      {j.last_run ? (
                        <Link to={`/runs/${j.last_run.id}`} className="row" style={{ gap: 6 }}>
                          {j.last_run.status === "completed" ? (
                            <StatusPill status={j.last_run.result_status} />
                          ) : (
                            <StatusPill status={j.last_run.status} />
                          )}
                          <span className="muted small">{fmtDate(j.last_run.finished_at || j.last_run.created_at)}</span>
                        </Link>
                      ) : (
                        <span className="muted">Never</span>
                      )}
                    </td>
                    <td className="r">
                      <Score value={j.last_run?.score} status={j.last_run?.result_status} />
                    </td>
                    <td className="r">
                      {canEdit && (
                        <button className="small primary" onClick={() => run(j)} disabled={starting === j.id}>
                          {starting === j.id ? "Starting…" : "Run now"}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
