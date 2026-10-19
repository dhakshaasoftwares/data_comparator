import { api, type RunT } from "../api";
import { ErrorBox, Loading, useData } from "../components/ui";
import { RunsTable } from "./Dashboard";

export default function Runs() {
  const runs = useData(() => api<RunT[]>("/runs?limit=200"));
  return (
    <div className="stack">
      <div className="page-head">
        <div>
          <h1>Run history</h1>
          <p>Every execution is kept with its configuration snapshot, for audit and sign-off.</p>
        </div>
        <button onClick={runs.reload}>Refresh</button>
      </div>
      <ErrorBox error={runs.error} />
      <div className="card flush">{runs.data ? <RunsTable runs={runs.data} /> : <Loading />}</div>
    </div>
  );
}
