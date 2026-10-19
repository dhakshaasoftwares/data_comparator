import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, fmtDate, fmtNum, fmtScore, type RunT } from "../api";
import { useAuth } from "../auth";
import { BarList } from "../components/charts";
import { ErrorBox, Loading, Score, StatusPill } from "../components/ui";

type Table = any;

function Cell({ v }: { v: unknown }) {
  if (v === null || v === undefined) return <span className="muted">NULL</span>;
  if (typeof v === "object") return <code>{JSON.stringify(v)}</code>;
  return <code>{String(v)}</code>;
}

function SampleTable({ rows }: { rows: Record<string, unknown>[] }) {
  if (!rows?.length) return <div className="muted small">None.</div>;
  const cols = Object.keys(rows[0]);
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {cols.map((c) => (
                <td key={c}>
                  <Cell v={r[c]} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Overview({ t }: { t: Table }) {
  const c = t.counts;
  const d = t.row_diff;
  return (
    <div className="grid cols-2">
      <div className="card">
        <h2>Score breakdown</h2>
        <BarList
          max={100}
          format={(n) => n.toFixed(1)}
          rows={t.components
            .filter((x: any) => x.applied)
            .map((x: any) => ({ label: `${x.name} (${x.weight.toFixed(0)}%)`, value: x.value, title: x.detail }))}
        />
        {t.components.some((x: any) => !x.applied) && (
          <div className="small muted" style={{ marginTop: 8 }}>
            Not measured: {t.components.filter((x: any) => !x.applied).map((x: any) => x.name).join(", ")}
          </div>
        )}
        <h3 style={{ marginTop: 18 }}>Why points were lost</h3>
        {t.deductions.length ? (
          <ul style={{ margin: 0, paddingLeft: 18 }}>
            {t.deductions.map((x: any) => (
              <li key={x.component} style={{ marginBottom: 6 }}>
                <b className="num">−{x.points.toFixed(1)}</b> <span className="ink-2">{x.component}:</span> {x.reason}
              </li>
            ))}
          </ul>
        ) : (
          <div className="alert ok">No deductions — source and target agree on everything that was checked.</div>
        )}
      </div>
      <div className="card">
        <h2>Rows</h2>
        <table>
          <tbody>
            <tr>
              <td className="ink-2">Source rows</td>
              <td className="r num">{fmtNum(c.source)}</td>
            </tr>
            <tr>
              <td className="ink-2">Target rows</td>
              <td className="r num">{fmtNum(c.target)}</td>
            </tr>
            <tr>
              <td className="ink-2">Difference</td>
              <td className="r num">{c.difference > 0 ? `+${fmtNum(c.difference)}` : fmtNum(c.difference)}</td>
            </tr>
            {d && (
              <>
                <tr>
                  <td className="ink-2">Matched {d.method === "key" ? `by key (${d.key_columns.join(", ")})` : "by row hash"}</td>
                  <td className="r num">{fmtNum(d.matched_rows)}</td>
                </tr>
                <tr>
                  <td className="ink-2">Identical</td>
                  <td className="r num">{fmtNum(d.identical_rows)}</td>
                </tr>
                <tr>
                  <td className="ink-2">Different values</td>
                  <td className="r num">{fmtNum(d.mismatched_rows)}</td>
                </tr>
                <tr>
                  <td className="ink-2">Missing in target</td>
                  <td className="r num">{fmtNum(d.only_in_source)}</td>
                </tr>
                <tr>
                  <td className="ink-2">Only in target</td>
                  <td className="r num">{fmtNum(d.only_in_target)}</td>
                </tr>
                {(d.duplicate_keys_source > 0 || d.duplicate_keys_target > 0) && (
                  <tr>
                    <td className="ink-2">Duplicate keys (source / target)</td>
                    <td className="r num">
                      {fmtNum(d.duplicate_keys_source)} / {fmtNum(d.duplicate_keys_target)}
                    </td>
                  </tr>
                )}
              </>
            )}
          </tbody>
        </table>
        {t.notes?.length > 0 && (
          <div className="stack small ink-2" style={{ gap: 4, marginTop: 12 }}>
            {t.notes.map((n: string) => (
              <div key={n}>ℹ {n}</div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function SchemaTab({ s }: { s: any }) {
  return (
    <div className="stack">
      <div className="row small">
        <span className="pill safe">✓ {s.summary.safe} safe</span>
        <span className="pill lossy">! {s.summary.lossy} lossy</span>
        <span className="pill incompatible">✕ {s.summary.incompatible} incompatible</span>
        <span className="chip">{s.summary.missing_in_target} missing in target</span>
        <span className="chip">{s.summary.extra_in_target} extra in target</span>
      </div>
      <div className="card flush">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Source column</th>
                <th>Source type</th>
                <th>Target column</th>
                <th>Target type</th>
                <th>Compatibility</th>
              </tr>
            </thead>
            <tbody>
              {s.columns.map((c: any) => (
                <tr key={c.source}>
                  <td>
                    <code>{c.source}</code>
                  </td>
                  <td className="mono ink-2">{c.source_type}</td>
                  <td>
                    <code>{c.target}</code>
                  </td>
                  <td className="mono ink-2">{c.target_type}</td>
                  <td>
                    <StatusPill status={c.compatibility} /> <span className="small ink-2">{c.reason}</span>
                    {c.warnings.map((w: string) => (
                      <div key={w} className="small muted">
                        {w}
                      </div>
                    ))}
                  </td>
                </tr>
              ))}
              {s.missing_in_target.map((m: any) => (
                <tr key={`m${m.column}`}>
                  <td>
                    <code>{m.column}</code>
                  </td>
                  <td className="mono ink-2">{m.type}</td>
                  <td className="muted">—</td>
                  <td />
                  <td>
                    <StatusPill status="fail" label="Missing in target" />
                  </td>
                </tr>
              ))}
              {s.extra_in_target.map((m: any) => (
                <tr key={`e${m.column}`}>
                  <td className="muted">—</td>
                  <td />
                  <td>
                    <code>{m.column}</code>
                  </td>
                  <td className="mono ink-2">{m.type}</td>
                  <td>
                    <span className="pill neutral">+ Target only</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function DiffTab({ d }: { d: any }) {
  if (!d) return <div className="alert">Row-level comparison was not run in this mode. Use “Row-level diff” in the options.</div>;
  const cols = d.columns.filter((c: any) => c.mismatches > 0).sort((a: any, b: any) => b.mismatches - a.mismatches);
  return (
    <div className="stack">
      {d.method === "key" && (
        <div className="card">
          <h2>Value differences by column</h2>
          {cols.length ? (
            <BarList rows={cols.map((c: any) => ({ label: c.source, value: c.mismatches, title: `${c.source} → ${c.target}: ${fmtNum(c.mismatches)} rows (${c.mismatch_pct}%)` }))} />
          ) : (
            <div className="alert ok">Every matched row has identical values.</div>
          )}
        </div>
      )}
      {d.samples.mismatches.length > 0 && (
        <div className="card flush">
          <div className="card-head" style={{ padding: "14px 16px 0" }}>
            <h2>Example differences</h2>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Key</th>
                  <th>Column</th>
                  <th>Source value</th>
                  <th>Target value</th>
                </tr>
              </thead>
              <tbody>
                {d.samples.mismatches.flatMap((m: any, i: number) =>
                  m.differences.map((x: any, j: number) => (
                    <tr key={`${i}-${j}`}>
                      <td>{j === 0 && <code>{Object.values(m.key).join(", ")}</code>}</td>
                      <td>{x.column}</td>
                      <td className="diff-old">
                        <Cell v={x.source} />
                      </td>
                      <td className="diff-new">
                        <Cell v={x.target} />
                      </td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <div className="grid cols-2">
        <div className="card">
          <h2>Missing in target ({fmtNum(d.only_in_source)})</h2>
          <SampleTable rows={d.samples.only_in_source} />
        </div>
        <div className="card">
          <h2>Only in target ({fmtNum(d.only_in_target)})</h2>
          <SampleTable rows={d.samples.only_in_target} />
        </div>
      </div>
    </div>
  );
}

function ProfileTab({ p }: { p: any }) {
  if (!p) return <div className="alert">Column statistics were not computed in this mode.</div>;
  return (
    <div className="card flush">
      <div className="card-head" style={{ padding: "14px 16px 0" }}>
        <h2>Column statistics</h2>
        <span className="small muted">
          {p.passed}/{p.checks} checks match · computed {p.computed}
        </span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Column</th>
              <th>Metric</th>
              <th>Source</th>
              <th>Target</th>
              <th>Match</th>
            </tr>
          </thead>
          <tbody>
            {p.columns.flatMap((c: any) =>
              c.error
                ? [
                    <tr key={c.source}>
                      <td>{c.source}</td>
                      <td colSpan={4} className="muted">
                        {c.error}
                      </td>
                    </tr>,
                  ]
                : c.metrics.map((m: any, i: number) => (
                    <tr key={`${c.source}-${m.metric}`}>
                      <td>{i === 0 && <code>{c.source}</code>}</td>
                      <td className="ink-2">{m.metric}</td>
                      <td className="num">
                        <Cell v={m.source} />
                      </td>
                      <td className="num">
                        <Cell v={m.target} />
                      </td>
                      <td>
                        {m.match ? (
                          <span className="pill pass">✓</span>
                        ) : m.scored ? (
                          <span className="pill fail">✕ differs</span>
                        ) : (
                          <span className="pill neutral" title="Depends on engine collation; not scored">
                            ≠ info
                          </span>
                        )}
                      </td>
                    </tr>
                  )),
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function QualityTab({ q }: { q: any[] | undefined }) {
  if (!q?.length) return <div className="alert">No data quality rules configured for this table.</div>;
  return (
    <div className="stack">
      {q.map((r, i) => (
        <div className="card" key={i}>
          <div className="spread">
            <div>
              <b>{r.rule}</b> <span className="muted small">on {r.side} · {r.severity} severity</span>
            </div>
            <StatusPill status={r.passed ? "pass" : "fail"} label={r.passed ? "Passed" : "Failed"} />
          </div>
          {r.error ? (
            <div className="alert error" style={{ marginTop: 8 }}>
              {r.error}
            </div>
          ) : (
            <>
              <div className="small ink-2" style={{ margin: "6px 0 10px" }}>
                {fmtNum(r.violations)} of {fmtNum(r.rows_checked)} rows violate the rule ({r.violation_pct}%, tolerated{" "}
                {r.threshold_pct}%)
              </div>
              {r.samples.length > 0 && (
                <details>
                  <summary className="small">Example rows</summary>
                  <SampleTable rows={r.samples} />
                </details>
              )}
            </>
          )}
        </div>
      ))}
    </div>
  );
}

function downloadCsv(run: RunT) {
  const head = ["source_table", "target_table", "status", "score", "source_rows", "target_rows", "missing_in_target",
    "only_in_target", "rows_with_differences", "schema_lossy", "schema_incompatible", "quality_failed", "top_issue"];
  const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = run.result.tables.map((t: Table) =>
    [t.source_table, t.target_table, t.status, t.score, t.counts?.source, t.counts?.target, t.row_diff?.only_in_source,
      t.row_diff?.only_in_target, t.row_diff?.mismatched_rows, t.schema?.summary.lossy, t.schema?.summary.incompatible,
      t.quality?.filter((q: any) => !q.passed).length, t.deductions?.[0]?.reason || t.error].map(esc).join(","),
  );
  const blob = new Blob([[head.join(","), ...lines].join("\n")], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `run-${run.id}.csv`;
  a.click();
}

export default function RunDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { canEdit } = useAuth();
  const [run, setRun] = useState<RunT | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selected, setSelected] = useState(0);
  const [tab, setTab] = useState("overview");

  useEffect(() => {
    let timer: number | undefined;
    let alive = true;
    const load = async () => {
      try {
        const r = await api<RunT>(`/runs/${id}`);
        if (!alive) return;
        setRun(r);
        if (r.status === "queued" || r.status === "running") timer = window.setTimeout(load, 1500);
      } catch (e) {
        if (alive) setError(e);
      }
    };
    load();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [id]);

  const rerun = async () => {
    try {
      const r = await api(`/jobs/${run!.job_id}/run`, { method: "POST" });
      navigate(`/runs/${r.id}`);
    } catch (e) {
      setError(e);
    }
  };

  if (!run) return error ? <ErrorBox error={error} /> : <Loading />;
  const active = run.status === "queued" || run.status === "running";
  const result = run.result;
  const t: Table | undefined = result?.tables?.[selected];
  const s = result?.summary;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <div className="small muted">
            <Link to="/runs">Run history</Link> / Run #{run.id}
          </div>
          <h1>{run.job_name}</h1>
          <p>
            Started {fmtDate(run.started_at || run.created_at)}
            {run.finished_at && ` · finished ${fmtDate(run.finished_at)}`}
            {result && ` · mode: ${result.mode}`}
          </p>
        </div>
        <div className="row">
          <Link className="btn" to={`/jobs/${run.job_id}`}>
            Edit configuration
          </Link>
          {result && (
            <>
              <button onClick={() => downloadCsv(run)}>Export CSV</button>
              <button onClick={() => window.print()}>Print / PDF</button>
            </>
          )}
          {canEdit && !active && (
            <button className="primary" onClick={rerun}>
              Run again
            </button>
          )}
        </div>
      </div>
      <ErrorBox error={error} />

      {active && (
        <div className="card">
          <div className="spread" style={{ marginBottom: 8 }}>
            <b>{run.status === "queued" ? "Waiting to start…" : `Comparing ${run.current_table || "…"}`}</b>
            <span className="num">{run.progress.toFixed(0)}%</span>
          </div>
          <div className="progress">
            <div style={{ width: `${Math.max(3, run.progress)}%` }} />
          </div>
        </div>
      )}
      {run.status === "failed" && <div className="alert error">Run failed: {run.error}</div>}

      {result && (
        <>
          <div className="grid cols-4">
            <div className="card stat">
              <div className="label">Readiness score</div>
              <div className="value" style={{ fontSize: 40 }}>
                <Score value={s.score} status={s.status} />
              </div>
              <StatusPill status={s.status} />
            </div>
            <div className="card stat">
              <div className="label">Tables</div>
              <div className="value num">{s.tables}</div>
              <div className="hint">
                {s.passed} pass · {s.warned} warning · {s.failed} fail{s.errors ? ` · ${s.errors} error` : ""}
              </div>
            </div>
            <div className="card stat">
              <div className="label">Source rows</div>
              <div className="value num">{fmtNum(s.source_rows)}</div>
            </div>
            <div className="card stat">
              <div className="label">Target rows</div>
              <div className="value num">{fmtNum(s.target_rows)}</div>
            </div>
          </div>

          <div className="card flush">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Source → target</th>
                    <th className="r">Score</th>
                    <th>Status</th>
                    <th className="r">Source rows</th>
                    <th className="r">Target rows</th>
                    <th>Biggest issue</th>
                  </tr>
                </thead>
                <tbody>
                  {result.tables.map((x: Table, i: number) => (
                    <tr
                      key={i}
                      className="clickable"
                      onClick={() => setSelected(i)}
                      style={i === selected ? { background: "var(--accent-soft)" } : undefined}
                    >
                      <td>
                        <code>{x.source_table}</code> → <code>{x.target_table}</code>
                      </td>
                      <td className="r">
                        <Score value={x.score} status={x.status} />
                      </td>
                      <td>
                        <StatusPill status={x.status} />
                      </td>
                      <td className="r num">{fmtNum(x.counts?.source)}</td>
                      <td className="r num">{fmtNum(x.counts?.target)}</td>
                      <td className="small ink-2">{x.error || x.deductions?.[0]?.reason || "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {t && (
            <div>
              <h2>
                <code>{t.source_table}</code> → <code>{t.target_table}</code>{" "}
                <span className="muted">· score {fmtScore(t.score)}</span>
              </h2>
              {t.status === "error" ? (
                <div className="alert error">{t.error}</div>
              ) : (
                <>
                  <div className="tabs" role="tablist">
                    {[
                      ["overview", "Overview"],
                      ["schema", "Schema"],
                      ["diff", "Data differences"],
                      ["profile", "Column statistics"],
                      ["quality", `Data quality${t.quality ? ` (${t.quality.filter((q: any) => !q.passed).length} failed)` : ""}`],
                    ].map(([k, label]) => (
                      <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>
                        {label}
                      </button>
                    ))}
                  </div>
                  {tab === "overview" && <Overview t={t} />}
                  {tab === "schema" && <SchemaTab s={t.schema} />}
                  {tab === "diff" && <DiffTab d={t.row_diff} />}
                  {tab === "profile" && <ProfileTab p={t.profile} />}
                  {tab === "quality" && <QualityTab q={t.quality} />}
                </>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
