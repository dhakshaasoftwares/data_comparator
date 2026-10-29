import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  api,
  defaultOptions,
  emptyPair,
  type ConnectionT,
  type JobConfig,
  type JobT,
  type QualityRule,
  type TablePair,
} from "../api";
import { useAuth } from "../auth";
import { ErrorBox, Help, Loading, StatusPill, useData, useToast } from "../components/ui";

const STEPS = ["Source & target", "Columns & keys", "Quality rules", "Options & scoring", "Config (JSON)"];

interface SchemaPreview {
  columns: {
    source: string;
    target: string;
    source_type: string;
    target_type: string;
    source_canonical: string;
    target_canonical: string;
    compatibility: string;
    reason: string;
    match: string;
    warnings: string[];
  }[];
  missing_in_target: { column: string; type: string }[];
  extra_in_target: { column: string; type: string }[];
  summary: Record<string, number>;
  score: number;
  suggested_keys: string[];
  source_columns: string[];
  target_columns: string[];
}

const RULE_TYPES: { type: QualityRule["type"]; label: string; help: string }[] = [
  { type: "not_null", label: "Not null", help: "Column must have a value" },
  { type: "unique", label: "Unique", help: "No duplicate values (comma-separate columns for a composite key)" },
  { type: "range", label: "Range", help: "Numeric value between min and max" },
  { type: "regex", label: "Pattern (regex)", help: "Whole value must match the regular expression" },
  { type: "allowed_values", label: "Allowed values", help: "Value must be one of a list" },
  { type: "max_length", label: "Max length", help: "Text no longer than N characters" },
  {
    type: "expression",
    label: "SQL expression",
    help: "Boolean SQL that is true for valid rows, e.g. end_date >= start_date",
  },
];

const newRule = (): QualityRule => ({
  type: "not_null",
  column: "",
  side: "target",
  severity: "medium",
  max_violation_pct: 0,
});

function blankConfig(): JobConfig {
  return { source_connection_id: 0, target_connection_id: 0, tables: [], options: defaultOptions() };
}

// ---------------------------------------------------------------------------

type ConfigSetter = (next: JobConfig | ((c: JobConfig) => JobConfig)) => void;

function StepTables({
  config,
  setConfig,
  conns,
}: {
  config: JobConfig;
  setConfig: ConfigSetter;
  conns: ConnectionT[];
}) {
  const [srcTables, setSrcTables] = useState<string[]>([]);
  const [tgtTables, setTgtTables] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [stale, setStale] = useState(false);

  useEffect(() => {
    setSrcTables([]);
    if (config.source_connection_id)
      api<string[]>(`/connections/${config.source_connection_id}/tables`).then(setSrcTables, setError);
  }, [config.source_connection_id]);
  useEffect(() => {
    setTgtTables([]);
    if (config.target_connection_id)
      api<string[]>(`/connections/${config.target_connection_id}/tables`).then(setTgtTables, setError);
  }, [config.target_connection_id]);

  const setPair = (i: number, patch: Partial<TablePair>) =>
    setConfig({ ...config, tables: config.tables.map((t, j) => (j === i ? { ...t, ...patch } : t)) });

  const autoPair = async (replace = false) => {
    setBusy(true);
    setError(null);
    setStale(false);
    try {
      const res = await api<{ pairs: TablePair[]; unmatched_source: string[]; unmatched_target: string[] }>(
        "/preview/auto-pair",
        {
          body: {
            source_connection_id: config.source_connection_id,
            target_connection_id: config.target_connection_id,
          },
        },
      );
      const current = replace ? [] : config.tables;
      const existing = new Set(current.map((t) => `${t.source_table}|${t.target_table}`));
      const added = res.pairs
        .filter((p) => !existing.has(`${p.source_table}|${p.target_table}`))
        .map((p) => emptyPair(p.source_table, p.target_table));
      setConfig((c) => ({ ...c, tables: [...(replace ? [] : c.tables), ...added] }));
      setNote(
        (added.length
          ? `Matched ${added.length} table(s) by name. Remove any you do not need, or add pairs whose names differ.`
          : "No new tables with matching names were found; add table pairs manually.") +
          (res.unmatched_source.length ? ` No target match for: ${res.unmatched_source.slice(0, 10).join(", ")}.` : ""),
      );
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  // New comparison: pair tables by name as soon as both sides are chosen.
  const autoTried = useRef("");
  useEffect(() => {
    const k = `${config.source_connection_id}-${config.target_connection_id}`;
    if (
      config.source_connection_id &&
      config.target_connection_id &&
      !config.tables.length &&
      autoTried.current !== k
    ) {
      autoTried.current = k;
      autoPair();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.source_connection_id, config.target_connection_id]);

  const connSelect = (key: "source_connection_id" | "target_connection_id") => (
    <select
      value={config[key] || ""}
      onChange={(e) => {
        const value = Number(e.target.value);
        setConfig((c) => ({ ...c, [key]: value }));
        if (config.tables.length) setStale(true);
      }}
      required
    >
      <option value="">Select a connection…</option>
      {conns.map((c) => (
        <option key={c.id} value={c.id}>
          {c.name} ({c.type})
        </option>
      ))}
    </select>
  );

  return (
    <div className="stack">
      <div className="form-grid">
        <label className="field">
          Source (migrating from)
          {connSelect("source_connection_id")}
        </label>
        <label className="field">
          Target (migrating to)
          {connSelect("target_connection_id")}
        </label>
      </div>
      <ErrorBox error={error} />
      {stale && config.source_connection_id > 0 && config.target_connection_id > 0 && (
        <div className="alert warn banner">
          <span>You changed a connection. The table names below may not exist there any more.</span>
          <button type="button" className="small" onClick={() => autoPair(true)} disabled={busy}>
            Replace with tables matched by name
          </button>
        </div>
      )}
      {note && !stale && <div className="alert ok">{note}</div>}
      <div className="card flush">
        <div className="card-head" style={{ padding: "14px 16px 0" }}>
          <h2>Tables to compare ({config.tables.length})</h2>
          <div className="row">
            <button
              type="button"
              onClick={() => autoPair()}
              disabled={busy || !config.source_connection_id || !config.target_connection_id}
            >
              Auto-pair all tables by name
            </button>
            <button type="button" onClick={() => setConfig({ ...config, tables: [...config.tables, emptyPair()] })}>
              Add table pair
            </button>
          </div>
        </div>
        <datalist id="src-tables">
          {srcTables.map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>
        <datalist id="tgt-tables">
          {tgtTables.map((t) => (
            <option key={t} value={t} />
          ))}
        </datalist>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Source table</th>
                <th>Target table</th>
                <th>Keys</th>
                <th>Rules</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {config.tables.map((t, i) => (
                <tr key={i}>
                  <td>
                    <input
                      list="src-tables"
                      value={t.source_table}
                      placeholder="schema.table"
                      onChange={(e) => setPair(i, { source_table: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      list="tgt-tables"
                      value={t.target_table}
                      placeholder="schema.table"
                      onChange={(e) => setPair(i, { target_table: e.target.value })}
                    />
                  </td>
                  <td className="ink-2 small">{t.key_columns.join(", ") || "automatic"}</td>
                  <td className="num">{t.rules.length}</td>
                  <td className="r">
                    <button
                      type="button"
                      className="small danger"
                      onClick={() => setConfig({ ...config, tables: config.tables.filter((_, j) => j !== i) })}
                    >
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
              {!config.tables.length && (
                <tr>
                  <td colSpan={5} className="muted">
                    Add table pairs manually, or use auto-pair to match every table by name (bulk mode).
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function PairPicker({ config, index, setIndex }: { config: JobConfig; index: number; setIndex: (i: number) => void }) {
  if (!config.tables.length) return <div className="alert">Add at least one table pair in step 1.</div>;
  return (
    <label className="field" style={{ maxWidth: 520 }}>
      Table pair
      <select value={index} onChange={(e) => setIndex(Number(e.target.value))}>
        {config.tables.map((t, i) => (
          <option key={i} value={i}>
            {t.source_table || "?"} → {t.target_table || "?"}
          </option>
        ))}
      </select>
    </label>
  );
}

function StepColumns({
  config,
  setPair,
  index,
  setIndex,
}: {
  config: JobConfig;
  setPair: (i: number, p: Partial<TablePair>) => void;
  index: number;
  setIndex: (i: number) => void;
}) {
  const pair = config.tables[index];
  const toast = useToast();
  const [detecting, setDetecting] = useState(false);
  const [preview, setPreview] = useState<SchemaPreview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);

  const deps = pair
    ? JSON.stringify([pair.source_table, pair.target_table, pair.column_mappings, pair.exclude_columns])
    : "";
  useEffect(() => {
    if (!pair?.source_table || !pair?.target_table || !config.source_connection_id || !config.target_connection_id)
      return;
    setLoading(true);
    api<SchemaPreview>("/preview/schema", {
      body: {
        source_connection_id: config.source_connection_id,
        target_connection_id: config.target_connection_id,
        source_table: pair.source_table,
        target_table: pair.target_table,
        column_mappings: pair.column_mappings,
        exclude_columns: pair.exclude_columns,
      },
    })
      .then((p) => {
        setPreview(p);
        setError(null);
      }, setError)
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deps, config.source_connection_id, config.target_connection_id]);

  if (!pair) return <PairPicker config={config} index={index} setIndex={setIndex} />;

  const detectKey = async () => {
    setDetecting(true);
    try {
      const r = await api<{ key_columns: string[] }>("/preview/detect-key", {
        body: {
          source_connection_id: config.source_connection_id,
          source_table: pair.source_table,
          source_filter: pair.source_filter,
        },
      });
      if (r.key_columns.length) {
        setPair(index, { key_columns: r.key_columns });
        toast(`Key column: ${r.key_columns.join(", ")} (unique in the source)`);
      } else toast("No single column is unique. Tick the columns that together identify a row.", "error");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setDetecting(false);
    }
  };

  const toggleKey = (col: string) =>
    setPair(index, {
      key_columns: pair.key_columns.includes(col)
        ? pair.key_columns.filter((k) => k !== col)
        : [...pair.key_columns, col],
    });
  const remap = (source: string, target: string) =>
    setPair(index, {
      column_mappings: [
        ...pair.column_mappings.filter((m) => m.source !== source),
        ...(target ? [{ source, target }] : []),
      ],
    });

  return (
    <div className="stack">
      <PairPicker config={config} index={index} setIndex={setIndex} />
      <ErrorBox error={error} />
      {loading && !preview && <Loading />}
      {preview && (
        <div style={{ opacity: loading ? 0.6 : 1 }} className="stack">
          <div className="row small">
            <span className="pill safe">✓ {preview.summary.safe} safe</span>
            <span className="pill lossy">! {preview.summary.lossy} lossy</span>
            <span className="pill incompatible">✕ {preview.summary.incompatible} incompatible</span>
            <span className="chip">{preview.summary.missing_in_target} missing in target</span>
            <span className="chip">{preview.summary.extra_in_target} extra in target</span>
            {!pair.key_columns.length && preview.suggested_keys.length > 0 && (
              <button
                type="button"
                className="small"
                onClick={() => setPair(index, { key_columns: preview.suggested_keys })}
              >
                Use primary key: {preview.suggested_keys.join(", ")}
              </button>
            )}
            {!pair.key_columns.length && !preview.suggested_keys.length && (
              <button type="button" className="small" onClick={detectKey} disabled={detecting}>
                {detecting ? "Looking for a unique column…" : "Detect key column"}
              </button>
            )}
          </div>
          <div className="card flush">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>
                      Key
                      <Help text="Key columns identify the same row in both tables (e.g. ORDER_ID). Leave empty and the run uses the primary key or detects a unique column automatically." />
                    </th>
                    <th>Source column</th>
                    <th>Source type</th>
                    <th>Target column</th>
                    <th>Target type</th>
                    <th>
                      Compatibility
                      <Help text="Safe: every source value fits. Lossy: some values may be rounded, truncated or lose time zone. Incompatible: the target type cannot hold the data." />
                    </th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {preview.columns.map((c) => (
                    <tr key={c.source}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Use ${c.source} as key`}
                          checked={pair.key_columns.includes(c.source)}
                          onChange={() => toggleKey(c.source)}
                        />
                      </td>
                      <td>
                        <code>{c.source}</code>
                      </td>
                      <td className="mono ink-2">{c.source_type}</td>
                      <td>
                        <select
                          value={c.target}
                          onChange={(e) => remap(c.source, e.target.value)}
                          style={{ minWidth: 140 }}
                        >
                          {preview.target_columns.map((t) => (
                            <option key={t}>{t}</option>
                          ))}
                        </select>
                        {c.match === "normalized" && <div className="muted small">matched by similar name</div>}
                      </td>
                      <td className="mono ink-2">{c.target_type}</td>
                      <td style={{ maxWidth: 340 }}>
                        <StatusPill status={c.compatibility} />
                        <div className="small ink-2">{c.reason}</div>
                        {c.warnings.map((w) => (
                          <div key={w} className="small muted">
                            {w}
                          </div>
                        ))}
                      </td>
                      <td className="r">
                        <button
                          type="button"
                          className="small ghost"
                          title="Exclude from comparison"
                          onClick={() => setPair(index, { exclude_columns: [...pair.exclude_columns, c.source] })}
                        >
                          Exclude
                        </button>
                      </td>
                    </tr>
                  ))}
                  {preview.missing_in_target.map((m) => (
                    <tr key={`m-${m.column}`}>
                      <td />
                      <td>
                        <code>{m.column}</code>
                      </td>
                      <td className="mono ink-2">{m.type}</td>
                      <td>
                        <select value="" onChange={(e) => remap(m.column, e.target.value)}>
                          <option value="">Not in target — map to…</option>
                          {preview.target_columns.map((t) => (
                            <option key={t}>{t}</option>
                          ))}
                        </select>
                      </td>
                      <td />
                      <td>
                        <StatusPill status="fail" label="Missing in target" />
                      </td>
                      <td className="r">
                        <button
                          type="button"
                          className="small ghost"
                          onClick={() => setPair(index, { exclude_columns: [...pair.exclude_columns, m.column] })}
                        >
                          Exclude
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          {preview.extra_in_target.length > 0 && (
            <div className="small ink-2">
              Target-only columns (not compared):{" "}
              {preview.extra_in_target.map((e) => (
                <span className="chip" key={e.column}>
                  {e.column}
                </span>
              ))}
            </div>
          )}
          {pair.exclude_columns.length > 0 && (
            <div className="small ink-2">
              Excluded:{" "}
              {pair.exclude_columns.map((c) => (
                <button
                  type="button"
                  key={c}
                  className="small"
                  style={{ marginRight: 4 }}
                  onClick={() => setPair(index, { exclude_columns: pair.exclude_columns.filter((x) => x !== c) })}
                >
                  {c} ✕
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="form-grid">
        <label className="field">
          Source filter (SQL WHERE, optional)
          <input
            className="mono"
            value={pair.source_filter || ""}
            placeholder="ORDER_DATE >= DATE '2024-01-01'"
            onChange={(e) => setPair(index, { source_filter: e.target.value || null })}
          />
        </label>
        <label className="field">
          Target filter (SQL WHERE, optional)
          <input
            className="mono"
            value={pair.target_filter || ""}
            placeholder="order_date >= '2024-01-01'"
            onChange={(e) => setPair(index, { target_filter: e.target.value || null })}
          />
        </label>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function RuleEditor({
  rule,
  onChange,
  onRemove,
}: {
  rule: QualityRule;
  onChange: (r: QualityRule) => void;
  onRemove: () => void;
}) {
  const set = (patch: Partial<QualityRule>) => onChange({ ...rule, ...patch });
  const meta = RULE_TYPES.find((r) => r.type === rule.type)!;
  return (
    <div className="card" style={{ padding: 14 }}>
      <div className="form-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))" }}>
        <label className="field">
          Rule
          <select value={rule.type} onChange={(e) => set({ type: e.target.value as QualityRule["type"] })}>
            {RULE_TYPES.map((r) => (
              <option key={r.type} value={r.type}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        {rule.type === "expression" ? (
          <label className="field" style={{ gridColumn: "span 2" }}>
            Expression
            <input
              className="mono"
              value={rule.expression || ""}
              onChange={(e) => set({ expression: e.target.value })}
            />
          </label>
        ) : (
          <label className="field">
            Column{rule.type === "unique" ? "(s)" : ""}
            <input
              list="rule-cols"
              value={rule.type === "unique" && rule.columns?.length ? rule.columns.join(", ") : rule.column || ""}
              onChange={(e) => {
                const v = e.target.value;
                if (rule.type === "unique" && v.includes(","))
                  set({
                    columns: v
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean),
                    column: null,
                  });
                else set({ column: v, columns: [] });
              }}
            />
          </label>
        )}
        {rule.type === "range" && (
          <>
            <label className="field">
              Min
              <input
                type="number"
                value={rule.min ?? ""}
                onChange={(e) => set({ min: e.target.value === "" ? null : Number(e.target.value) })}
              />
            </label>
            <label className="field">
              Max
              <input
                type="number"
                value={rule.max ?? ""}
                onChange={(e) => set({ max: e.target.value === "" ? null : Number(e.target.value) })}
              />
            </label>
          </>
        )}
        {rule.type === "regex" && (
          <label className="field">
            Pattern
            <input className="mono" value={rule.pattern || ""} onChange={(e) => set({ pattern: e.target.value })} />
          </label>
        )}
        {rule.type === "allowed_values" && (
          <label className="field">
            Values (comma separated)
            <input
              value={(rule.values || []).join(", ")}
              onChange={(e) =>
                set({
                  values: e.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                })
              }
            />
          </label>
        )}
        {rule.type === "max_length" && (
          <label className="field">
            Length
            <input type="number" value={rule.length ?? ""} onChange={(e) => set({ length: Number(e.target.value) })} />
          </label>
        )}
        <label className="field">
          Check on
          <select value={rule.side} onChange={(e) => set({ side: e.target.value as QualityRule["side"] })}>
            <option value="target">Target</option>
            <option value="source">Source</option>
            <option value="both">Both</option>
          </select>
        </label>
        <label className="field">
          Severity
          <select value={rule.severity} onChange={(e) => set({ severity: e.target.value as QualityRule["severity"] })}>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </label>
        <label className="field">
          Tolerated bad rows %
          <input
            type="number"
            min={0}
            max={100}
            step="0.01"
            value={rule.max_violation_pct}
            onChange={(e) => set({ max_violation_pct: Number(e.target.value) })}
          />
        </label>
      </div>
      <div className="spread" style={{ marginTop: 10 }}>
        <span className="small muted">{meta.help}</span>
        <button type="button" className="small danger" onClick={onRemove}>
          Remove rule
        </button>
      </div>
    </div>
  );
}

function StepRules({
  config,
  setPair,
  index,
  setIndex,
}: {
  config: JobConfig;
  setPair: (i: number, p: Partial<TablePair>) => void;
  index: number;
  setIndex: (i: number) => void;
}) {
  const pair = config.tables[index];
  const [cols, setCols] = useState<string[]>([]);
  useEffect(() => {
    if (!pair?.target_table || !config.target_connection_id) return;
    api<{ columns: { name: string }[] }>(
      `/connections/${config.target_connection_id}/columns?table=${encodeURIComponent(pair.target_table)}`,
    ).then(
      (r) => setCols(r.columns.map((c) => c.name)),
      () => setCols([]),
    );
  }, [pair?.target_table, config.target_connection_id]);

  if (!pair) return <PairPicker config={config} index={index} setIndex={setIndex} />;
  const setRules = (rules: QualityRule[]) => setPair(index, { rules });
  return (
    <div className="stack">
      <PairPicker config={config} index={index} setIndex={setIndex} />
      <datalist id="rule-cols">
        {cols.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
      {pair.rules.map((r, i) => (
        <RuleEditor
          key={i}
          rule={r}
          onChange={(nr) => setRules(pair.rules.map((x, j) => (j === i ? nr : x)))}
          onRemove={() => setRules(pair.rules.filter((_, j) => j !== i))}
        />
      ))}
      {!pair.rules.length && <div className="muted">No rules for this table yet.</div>}
      <div>
        <button type="button" onClick={() => setRules([...pair.rules, newRule()])}>
          Add rule
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function StepOptions({ config, setConfig }: { config: JobConfig; setConfig: (c: JobConfig) => void }) {
  const o = config.options;
  const set = (patch: Partial<typeof o>) => setConfig({ ...config, options: { ...o, ...patch } });
  const setNorm = (patch: Partial<typeof o.normalization>) => set({ normalization: { ...o.normalization, ...patch } });
  const modes = [
    [
      "counts",
      "Schema + row counts",
      "Fastest. Nothing but metadata and COUNT(*) queries; no data leaves the engines.",
    ],
    [
      "profile",
      "Column statistics",
      "Nulls, distinct, min, max and sum per column, computed inside each engine (pushdown).",
    ],
    ["full", "Row-level diff", "Matches every row by key and compares values. Large tables are sampled by key range."],
  ] as const;
  const totalW = o.weights.completeness + o.weights.accuracy + o.weights.schema + o.weights.quality;
  return (
    <div className="stack">
      <div className="card">
        <h2>Comparison depth</h2>
        <div className="grid cols-3">
          {modes.map(([value, label, help]) => (
            <label
              key={value}
              className="card"
              style={{ cursor: "pointer", borderColor: o.mode === value ? "var(--accent)" : undefined }}
            >
              <span className="check">
                <input type="radio" name="mode" checked={o.mode === value} onChange={() => set({ mode: value })} />
                <b>{label}</b>
              </span>
              <div className="small ink-2" style={{ marginTop: 6 }}>
                {help}
              </div>
            </label>
          ))}
        </div>
        <div className="form-grid" style={{ marginTop: 14 }}>
          <label className="field">
            Max rows per table for row-level diff
            <input
              type="number"
              min={1}
              value={o.max_rows ?? ""}
              placeholder="No limit"
              onChange={(e) => set({ max_rows: e.target.value === "" ? null : Number(e.target.value) })}
            />
            <span className="help">Above this, the first N rows by key are compared (your plan may cap it).</span>
          </label>
          <label className="field">
            Mismatch examples to keep
            <input
              type="number"
              min={0}
              max={500}
              value={o.sample_size}
              onChange={(e) => set({ sample_size: Number(e.target.value) })}
            />
          </label>
        </div>
      </div>
      <div className="card">
        <h2>Normalisation (what counts as “the same”)</h2>
        <div className="form-grid">
          <label className="check">
            <input
              type="checkbox"
              checked={o.normalization.trim_strings}
              onChange={(e) => setNorm({ trim_strings: e.target.checked })}
            />
            Ignore leading/trailing spaces (CHAR padding)
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={o.normalization.case_insensitive}
              onChange={(e) => setNorm({ case_insensitive: e.target.checked })}
            />
            Case-insensitive text
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={o.normalization.null_equals_empty}
              onChange={(e) => setNorm({ null_equals_empty: e.target.checked })}
            />
            Treat empty string as NULL (Oracle semantics)
          </label>
          <span />
          <label className="field">
            Numeric tolerance (absolute)
            <input
              type="number"
              min={0}
              step="any"
              value={o.normalization.numeric_tolerance}
              onChange={(e) => setNorm({ numeric_tolerance: Number(e.target.value) })}
            />
          </label>
          <label className="field">
            Timestamp precision
            <select
              value={o.normalization.timestamp_precision}
              onChange={(e) =>
                setNorm({ timestamp_precision: e.target.value as typeof o.normalization.timestamp_precision })
              }
            >
              <option value="microsecond">Microsecond</option>
              <option value="millisecond">Millisecond</option>
              <option value="second">Second</option>
            </select>
          </label>
        </div>
      </div>
      <div className="card">
        <h2>Readiness score</h2>
        <p className="small ink-2" style={{ marginTop: 0 }}>
          Weights are relative ({totalW} total); components that are not measured are left out and the rest re-balanced.
        </p>
        <div className="form-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))" }}>
          {(["completeness", "accuracy", "schema", "quality"] as const).map((k) => (
            <label className="field" key={k}>
              {k[0].toUpperCase() + k.slice(1)} weight
              <input
                type="number"
                min={0}
                value={o.weights[k]}
                onChange={(e) => set({ weights: { ...o.weights, [k]: Number(e.target.value) } })}
              />
            </label>
          ))}
          <label className="field">
            Pass at score ≥
            <input
              type="number"
              min={0}
              max={100}
              value={o.thresholds.pass_score}
              onChange={(e) => set({ thresholds: { ...o.thresholds, pass_score: Number(e.target.value) } })}
            />
          </label>
          <label className="field">
            Warn at score ≥
            <input
              type="number"
              min={0}
              max={100}
              value={o.thresholds.warn_score}
              onChange={(e) => set({ thresholds: { ...o.thresholds, warn_score: Number(e.target.value) } })}
            />
          </label>
        </div>
      </div>
    </div>
  );
}

function StepJson({ config, setConfig, name }: { config: JobConfig; setConfig: (c: JobConfig) => void; name: string }) {
  const text = useMemo(() => JSON.stringify(config, null, 2), [config]);
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${name.replace(/[^a-z0-9]+/gi, "_") || "comparison"}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const apply = () => {
    try {
      const parsed = JSON.parse(draft || "");
      setConfig({ ...blankConfig(), ...parsed, options: { ...defaultOptions(), ...(parsed.options || {}) } });
      setDraft(null);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <div className="stack">
      <p className="ink-2" style={{ margin: 0 }}>
        The whole comparison is one JSON document: keep it in Git, review it in pull requests, or paste one here to
        import it.
      </p>
      <textarea
        className="mono"
        style={{ minHeight: 360 }}
        value={draft ?? text}
        onChange={(e) => setDraft(e.target.value)}
      />
      {error && <div className="alert error">{error}</div>}
      <div className="row">
        <button type="button" onClick={download}>
          Download JSON
        </button>
        {draft !== null && (
          <>
            <button type="button" className="primary" onClick={apply}>
              Apply changes
            </button>
            <button type="button" onClick={() => setDraft(null)}>
              Discard
            </button>
          </>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

interface Recommendation {
  title: string;
  normalization: Partial<JobConfig["options"]["normalization"]>;
  reasons: string[];
}

function RecommendationBanner({
  rec,
  applied,
  onApply,
}: {
  rec: Recommendation;
  applied: boolean;
  onApply: () => void;
}) {
  return (
    <div className="alert banner">
      <div>
        <b>{rec.title}</b>
        {applied && (
          <span className="pill pass" style={{ marginLeft: 8 }}>
            ✓ applied
          </span>
        )}
        <ul className="small">
          {rec.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      </div>
      {!applied && (
        <button className="small" onClick={onApply}>
          Apply recommended settings
        </button>
      )}
    </div>
  );
}

export default function JobEditor() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { canEdit } = useAuth();
  const conns = useData(() => api<ConnectionT[]>("/connections"));
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [config, setConfig] = useState<JobConfig>(blankConfig);
  const [loaded, setLoaded] = useState(!id);
  const [step, setStep] = useState(0);
  const [pairIndex, setPairIndex] = useState(0);
  const [error, setError] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [nameTouched, setNameTouched] = useState(!!id);
  const [rec, setRec] = useState<Recommendation | null>(null);
  const [showIssues, setShowIssues] = useState(false);
  const toast = useToast();

  const applyRec = (r: Recommendation) =>
    setConfig((c) => ({
      ...c,
      options: { ...c.options, normalization: { ...c.options.normalization, ...r.normalization } },
    }));

  // Platform-specific defaults (Oracle '' = NULL, CHAR padding, ...), applied automatically to new comparisons.
  useEffect(() => {
    setRec(null);
    const { source_connection_id: s, target_connection_id: t } = config;
    if (!s || !t || !loaded) return;
    api<Recommendation>(`/preview/recommendations?source_connection_id=${s}&target_connection_id=${t}`).then(
      (r) => {
        setRec(r);
        if (!id) applyRec(r);
      },
      () => undefined,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config.source_connection_id, config.target_connection_id, loaded]);

  // Suggest a name from the two connections until the user types one.
  useEffect(() => {
    if (nameTouched || !conns.data) return;
    const s = conns.data.find((c) => c.id === config.source_connection_id);
    const t = conns.data.find((c) => c.id === config.target_connection_id);
    if (s && t) setName(`${s.name} → ${t.name}`);
  }, [config.source_connection_id, config.target_connection_id, conns.data, nameTouched]);

  const issues: string[] = [];
  if (!config.source_connection_id) issues.push("Choose a source connection (step 1).");
  if (!config.target_connection_id) issues.push("Choose a target connection (step 1).");
  if (!config.tables.length) issues.push("Add at least one table pair (step 1).");
  config.tables.forEach((t, i) => {
    if (!t.source_table || !t.target_table) issues.push(`Table pair ${i + 1} needs both a source and a target table.`);
  });

  useEffect(() => {
    if (!id) return;
    api<JobT>(`/jobs/${id}`).then((j) => {
      setName(j.name);
      setDescription(j.description);
      setConfig({ ...j.config, options: { ...defaultOptions(), ...j.config.options } });
      setLoaded(true);
    }, setError);
  }, [id]);

  const update = (next: JobConfig | ((c: JobConfig) => JobConfig)) => {
    setConfig((prev) => {
      const c = typeof next === "function" ? next(prev) : next;
      if (pairIndex >= c.tables.length) setPairIndex(Math.max(0, c.tables.length - 1));
      return c;
    });
    setSaved(false);
  };
  const setPair = (i: number, patch: Partial<TablePair>) =>
    update({ ...config, tables: config.tables.map((t, j) => (j === i ? { ...t, ...patch } : t)) });

  const save = async (andRun: boolean) => {
    if (issues.length) {
      setShowIssues(true);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const body = { name: name || "Untitled comparison", description, config };
      const job = id ? await api<JobT>(`/jobs/${id}`, { method: "PUT", body }) : await api<JobT>("/jobs", { body });
      setSaved(true);
      toast(andRun ? "Saved. Starting the comparison…" : "Comparison saved");
      if (andRun) {
        const run = await api(`/jobs/${job.id}/run`, { method: "POST" });
        navigate(`/runs/${run.id}`);
      } else if (!id) navigate(`/jobs/${job.id}`, { replace: true });
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!id || !confirm("Delete this comparison and all its runs?")) return;
    try {
      await api(`/jobs/${id}`, { method: "DELETE" });
      navigate("/jobs");
    } catch (e) {
      setError(e);
    }
  };

  if (!loaded || !conns.data) return error ? <ErrorBox error={error} /> : <Loading />;

  return (
    <div className="stack">
      <div className="page-head">
        <div style={{ flex: 1, minWidth: 260 }}>
          <input
            aria-label="Comparison name"
            value={name}
            placeholder="Comparison name, e.g. Oracle SALES → Databricks"
            onChange={(e) => {
              setName(e.target.value);
              setNameTouched(true);
              setSaved(false);
            }}
            style={{ fontSize: 20, fontWeight: 650, border: "none", background: "transparent", padding: "2px 0" }}
          />
          <input
            aria-label="Description"
            value={description}
            placeholder="Description (optional)"
            onChange={(e) => setDescription(e.target.value)}
            style={{ border: "none", background: "transparent", padding: "2px 0", color: "var(--ink-2)" }}
          />
        </div>
        {canEdit && (
          <div className="row">
            {id && (
              <button className="danger" onClick={remove}>
                Delete
              </button>
            )}
            <button onClick={() => save(false)} disabled={saving}>
              {saved ? "Saved ✓" : "Save"}
            </button>
            <button className="primary" onClick={() => save(true)} disabled={saving}>
              Save & run
            </button>
          </div>
        )}
      </div>
      <ErrorBox error={error} />
      {showIssues && issues.length > 0 && (
        <div className="alert warn">
          <b>Almost there:</b>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {issues.map((i) => (
              <li key={i}>{i}</li>
            ))}
          </ul>
        </div>
      )}
      {conns.data.length < 2 && (
        <div className="alert warn">
          You need a source and a target connection. <a href="/connections">Add connections</a> first.
        </div>
      )}
      <div className="steps" role="tablist">
        {STEPS.map((s, i) => (
          <button
            key={s}
            role="tab"
            aria-selected={step === i}
            className={step === i ? "active" : ""}
            onClick={() => setStep(i)}
          >
            {i === 0 && config.tables.length > 0 && config.tables.every((t) => t.source_table && t.target_table)
              ? "✓"
              : `${i + 1}.`}{" "}
            {s}
          </button>
        ))}
      </div>
      {rec && (step === 0 || step === 3) && (
        <RecommendationBanner
          rec={rec}
          applied={Object.entries(rec.normalization).every(
            ([k, v]) => config.options.normalization[k as keyof typeof config.options.normalization] === v,
          )}
          onApply={() => {
            applyRec(rec);
            setSaved(false);
            toast("Recommended settings applied");
          }}
        />
      )}
      {step === 0 && <StepTables config={config} setConfig={update} conns={conns.data} />}
      {step === 1 && <StepColumns config={config} setPair={setPair} index={pairIndex} setIndex={setPairIndex} />}
      {step === 2 && <StepRules config={config} setPair={setPair} index={pairIndex} setIndex={setPairIndex} />}
      {step === 3 && <StepOptions config={config} setConfig={update} />}
      {step === 4 && <StepJson config={config} setConfig={update} name={name} />}
      <div className="spread">
        <button disabled={step === 0} onClick={() => setStep(step - 1)}>
          ← Back
        </button>
        {step < STEPS.length - 1 && (
          <button className="primary" onClick={() => setStep(step + 1)}>
            Next: {STEPS[step + 1]} →
          </button>
        )}
      </div>
    </div>
  );
}
