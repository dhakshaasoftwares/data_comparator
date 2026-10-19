import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorBox, Loading, useData } from "../components/ui";

interface Member {
  id: number;
  email: string;
  name: string;
  role: string;
  is_active: boolean;
}

export default function Team() {
  const { user } = useAuth();
  const members = useData(() => api<Member[]>("/users"));
  const [form, setForm] = useState({ email: "", name: "", password: "", role: "editor" });
  const [error, setError] = useState<unknown>(null);
  const isAdmin = user?.role === "admin";

  const add = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await api("/users", { body: form });
      setForm({ email: "", name: "", password: "", role: "editor" });
      members.reload();
    } catch (err) {
      setError(err);
    }
  };

  const remove = async (m: Member) => {
    if (!confirm(`Deactivate ${m.email}?`)) return;
    try {
      await api(`/users/${m.id}`, { method: "DELETE" });
      members.reload();
    } catch (err) {
      setError(err);
    }
  };

  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Team</h1>
          <p>Admins manage users and billing, editors configure and run comparisons, viewers read results.</p>
        </div>
      </div>
      <ErrorBox error={error} />
      <div className="card flush">
        {!members.data ? (
          <Loading />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Name</th>
                  <th>Role</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {members.data.map((m) => (
                  <tr key={m.id}>
                    <td>{m.email}</td>
                    <td>{m.name || "–"}</td>
                    <td>{m.role}</td>
                    <td className="ink-2">{m.is_active ? "Active" : "Deactivated"}</td>
                    <td className="r">
                      {isAdmin && m.is_active && m.id !== user?.id && (
                        <button className="small danger" onClick={() => remove(m)}>
                          Deactivate
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
      {isAdmin && (
        <form className="card" onSubmit={add}>
          <h2>Add a team member</h2>
          <div className="form-grid">
            <label className="field">
              Email
              <input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </label>
            <label className="field">
              Name
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label className="field">
              Temporary password
              <input
                type="password"
                required
                minLength={8}
                autoComplete="new-password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
              />
            </label>
            <label className="field">
              Role
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                <option value="viewer">Viewer</option>
                <option value="editor">Editor</option>
                <option value="admin">Admin</option>
              </select>
            </label>
          </div>
          <div style={{ marginTop: 14 }}>
            <button className="primary">Add member</button>
          </div>
        </form>
      )}
    </div>
  );
}
