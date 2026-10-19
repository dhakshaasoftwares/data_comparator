import { useMemo, useState } from "react";
import { api, fmtDate, type ConfigField, type ConnectionT, type ConnectorType } from "../api";
import { useAuth } from "../auth";
import { Empty, ErrorBox, Loading, Modal, useData } from "../components/ui";

const CATEGORY_ORDER = ["Cloud warehouse", "Big data", "Database", "Files"];

function FieldInput({
  field,
  value,
  stored,
  onChange,
}: {
  field: ConfigField;
  value: any;
  stored: boolean;
  onChange: (v: any) => void;
}) {
  const placeholder = stored ? "•••••• (stored — leave blank to keep)" : field.placeholder;
  if (field.type === "boolean")
    return (
      <label className="check full">
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
        {field.label}
      </label>
    );
  let input;
  if (field.type === "select")
    input = (
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value)}>
        {field.options.map((o) => (
          <option key={o}>{o}</option>
        ))}
      </select>
    );
  else if (field.type === "textarea")
    input = <textarea value={value ?? ""} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />;
  else
    input = (
      <input
        type={field.type === "password" ? "password" : field.type === "number" ? "number" : "text"}
        value={value ?? ""}
        placeholder={placeholder}
        required={field.required && !(field.secret && stored)}
        autoComplete={field.type === "password" ? "new-password" : "off"}
        onChange={(e) => onChange(field.type === "number" ? (e.target.value === "" ? "" : Number(e.target.value)) : e.target.value)}
      />
    );
  return (
    <label className={`field ${field.type === "textarea" ? "full" : ""}`}>
      <span>
        {field.label}
        {field.required && <span className="muted"> *</span>}
      </span>
      {input}
      {field.help && <span className="help">{field.help}</span>}
    </label>
  );
}

function ConnectionForm({
  types,
  existing,
  onClose,
  onSaved,
}: {
  types: ConnectorType[];
  existing: ConnectionT | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [type, setType] = useState<ConnectorType | null>(existing ? types.find((t) => t.type === existing.type) || null : null);
  const [name, setName] = useState(existing?.name || "");
  const [config, setConfig] = useState<Record<string, any>>(existing?.config || {});
  const [test, setTest] = useState<{ ok: boolean; message: string } | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  const pick = (t: ConnectorType) => {
    setType(t);
    setTest(null);
    const defaults: Record<string, any> = {};
    t.fields.forEach((f) => f.default !== null && f.default !== undefined && (defaults[f.name] = f.default));
    setConfig(defaults);
    if (!name) setName(t.label.split(" (")[0]);
  };

  const runTest = async () => {
    setBusy(true);
    setTest(null);
    try {
      setTest(await api("/connections/test", { body: { type: type!.type, config, connection_id: existing?.id } }));
    } catch (e) {
      setTest({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = { name, type: type!.type, config };
      if (existing) await api(`/connections/${existing.id}`, { method: "PUT", body });
      else await api("/connections", { body });
      onSaved();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const grouped = useMemo(
    () => CATEGORY_ORDER.map((c) => [c, types.filter((t) => t.category === c)] as const).filter(([, l]) => l.length),
    [types],
  );

  if (!type)
    return (
      <Modal title="New connection" onClose={onClose}>
        <div className="stack">
          {grouped.map(([cat, list]) => (
            <div key={cat}>
              <h3>{cat}</h3>
              <div className="type-grid">
                {list.map((t) => (
                  <button key={t.type} className="type-card" onClick={() => pick(t)}>
                    <div style={{ fontWeight: 600 }}>{t.label}</div>
                    <div className="cat">{t.driver_installed ? t.description || t.category : `Driver needed: ${t.driver_hint}`}</div>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Modal>
    );

  return (
    <Modal title={existing ? `Edit ${existing.name}` : `New ${type.label} connection`} onClose={onClose}>
      <form onSubmit={save} className="stack">
        {!type.driver_installed && (
          <div className="alert warn">
            The server does not have the driver for {type.label}. Install it with <code>pip install {type.driver_hint}</code>{" "}
            (or use the Docker image with all connectors).
          </div>
        )}
        {type.description && <div className="ink-2">{type.description}</div>}
        <div className="form-grid">
          <label className="field full">
            Connection name
            <input value={name} onChange={(e) => setName(e.target.value)} required />
          </label>
          {type.fields.map((f) => (
            <FieldInput
              key={f.name}
              field={f}
              value={config[f.name]}
              stored={!!existing?.secrets_set.includes(f.name)}
              onChange={(v) => setConfig({ ...config, [f.name]: v })}
            />
          ))}
        </div>
        {test && <div className={`alert ${test.ok ? "ok" : "error"}`}>{test.message}</div>}
        <ErrorBox error={error} />
        <div className="spread">
          {!existing ? (
            <button type="button" className="ghost" onClick={() => setType(null)}>
              ← Change type
            </button>
          ) : (
            <span />
          )}
          <div className="row">
            <button type="button" onClick={runTest} disabled={busy}>
              Test connection
            </button>
            <button className="primary" disabled={busy}>
              Save
            </button>
          </div>
        </div>
      </form>
    </Modal>
  );
}

export default function Connections() {
  const { canEdit } = useAuth();
  const conns = useData(() => api<ConnectionT[]>("/connections"));
  const types = useData(() => api<ConnectorType[]>("/connector-types"));
  const [editing, setEditing] = useState<ConnectionT | null | "new">(null);
  const [error, setError] = useState<unknown>(null);

  const remove = async (c: ConnectionT) => {
    if (!confirm(`Delete connection "${c.name}"?`)) return;
    try {
      await api(`/connections/${c.id}`, { method: "DELETE" });
      conns.reload();
    } catch (e) {
      setError(e);
    }
  };

  const typeLabel = (t: string) => types.data?.find((x) => x.type === t)?.label || t;

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Connections</h1>
          <p>Sources and targets: databases, warehouses, Hive/Spark and data-lake files. Secrets are encrypted at rest.</p>
        </div>
        {canEdit && (
          <button className="primary" onClick={() => setEditing("new")} disabled={!types.data}>
            Add connection
          </button>
        )}
      </div>
      <ErrorBox error={error || conns.error} />
      <div className="card flush">
        {!conns.data ? (
          <Loading />
        ) : conns.data.length === 0 ? (
          <Empty title="No connections yet">
            <p>Add your source system (for example Oracle) and your target (for example Databricks).</p>
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th>Details</th>
                  <th>Updated</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {conns.data.map((c) => (
                  <tr key={c.id}>
                    <td style={{ fontWeight: 560 }}>{c.name}</td>
                    <td>{typeLabel(c.type)}</td>
                    <td className="ink-2 small">
                      {Object.entries(c.config)
                        .filter(([, v]) => v !== "" && v !== null && typeof v !== "boolean")
                        .slice(0, 3)
                        .map(([k, v]) => (
                          <span className="chip" key={k}>
                            {k}: {String(v)}
                          </span>
                        ))}
                    </td>
                    <td className="ink-2">{fmtDate(c.updated_at)}</td>
                    <td className="r">
                      {canEdit && (
                        <div className="row" style={{ justifyContent: "flex-end", flexWrap: "nowrap" }}>
                          <button className="small" onClick={() => setEditing(c)}>
                            Edit
                          </button>
                          <button className="small danger" onClick={() => remove(c)}>
                            Delete
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {editing && types.data && (
        <ConnectionForm
          types={types.data}
          existing={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            conns.reload();
          }}
        />
      )}
    </div>
  );
}
