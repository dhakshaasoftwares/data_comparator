import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { fmtScore } from "../api";

const STATUS_LABEL: Record<string, [string, string]> = {
  pass: ["✓", "Pass"],
  warn: ["!", "Warning"],
  fail: ["✕", "Fail"],
  error: ["✕", "Error"],
  safe: ["✓", "Safe"],
  lossy: ["!", "Lossy"],
  incompatible: ["✕", "Incompatible"],
  completed: ["✓", "Completed"],
  failed: ["✕", "Failed"],
  queued: ["…", "Queued"],
  running: ["↻", "Running"],
};

/** Status is always shown as icon + label, never colour alone. */
export function StatusPill({ status, label }: { status: string | null | undefined; label?: string }) {
  if (!status) return <span className="pill neutral">Not run</span>;
  const [ico, text] = STATUS_LABEL[status] || ["•", status];
  return (
    <span className={`pill ${status}`}>
      <span className="ico" aria-hidden>
        {ico}
      </span>
      {label || text}
    </span>
  );
}

export function Score({ value, status }: { value: number | null | undefined; status?: string | null }) {
  const color =
    status === "pass"
      ? "var(--good-ink)"
      : status === "warn"
        ? "var(--warning-ink)"
        : status
          ? "var(--critical-ink)"
          : undefined;
  return (
    <span className="score" style={{ color }}>
      {fmtScore(value)}
    </span>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-back" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-label={title}>
        <div className="spread" style={{ marginBottom: 14 }}>
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button className="ghost" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="alert error">{error instanceof Error ? error.message : String(error)}</div>;
}

export function Loading() {
  return (
    <div className="muted" style={{ padding: 24 }}>
      Loading…
    </div>
  );
}

/** Load data on mount; `reload` re-fetches while keeping the previous data on screen. */
export function useData<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const load = useCallback(loader, deps);
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await load());
      setError(null);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [load]);
  useEffect(() => {
    reload();
  }, [reload]);
  return { data, error, loading, reload, setData };
}

/** Small "?" that explains a term on hover and keyboard focus. */
export function Help({ text }: { text: string }) {
  return (
    <span className="help-tip" tabIndex={0} role="note" aria-label={text} data-tip={text}>
      ?
    </span>
  );
}

/** Change of score since the previous run of the same comparison. */
export function ScoreDelta({
  score,
  previous,
}: {
  score: number | null | undefined;
  previous: number | null | undefined;
}) {
  if (score === null || score === undefined || previous === null || previous === undefined) return null;
  const d = score - previous;
  if (Math.abs(d) < 0.05) return <span className="delta same">no change</span>;
  return (
    <span className={`delta ${d > 0 ? "up" : "down"}`} title={`Previous run: ${fmtScore(previous)}`}>
      {d > 0 ? "▲" : "▼"} {Math.abs(d).toFixed(1)}
    </span>
  );
}

// ---- toasts ------------------------------------------------------------------

type Toast = { id: number; text: string; kind: "ok" | "error" };
const ToastContext = createContext<(text: string, kind?: Toast["kind"]) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const show = useCallback((text: string, kind: Toast["kind"] = "ok") => {
    const id = next.current++;
    setToasts((t) => [...t, { id, text, kind }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            {t.kind === "ok" ? "✓" : "✕"} {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}
