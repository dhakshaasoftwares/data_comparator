import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { applyFixAndRun, type Insight } from "../api";
import { useAuth } from "../auth";
import { useToast } from "./ui";

const ICON = { critical: "✕", warning: "!", info: "i" } as const;

/** Findings in plain language, each with an optional one-click fix that re-runs the comparison. */
export default function Insights({
  items,
  jobId,
  showTable = false,
  limit,
}: {
  items: Insight[];
  jobId: number;
  showTable?: boolean;
  limit?: number;
}) {
  const navigate = useNavigate();
  const toast = useToast();
  const { canEdit } = useAuth();
  const [busy, setBusy] = useState<number | null>(null);
  const [expanded, setExpanded] = useState(false);

  if (!items.length)
    return <div className="alert ok">Nothing to fix — source and target agree on everything that was checked.</div>;

  const shown = limit && !expanded ? items.slice(0, limit) : items;

  const apply = async (i: number, item: Insight) => {
    const what =
      item.fix!.kind === "option"
        ? "This setting applies to every table in this comparison."
        : `This changes the configuration of ${item.source_table}.`;
    if (!confirm(`${item.fix!.label}?\n\n${what} A new run starts right away.`)) return;
    setBusy(i);
    try {
      const run = await applyFixAndRun(jobId, item.fix!, item);
      toast(`Applied “${item.fix!.label}”. Re-running…`);
      navigate(`/runs/${run.id}`);
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      {shown.map((item, i) => (
        <div key={i} className={`insight ${item.level}`}>
          <span className="ico" aria-label={item.level}>
            {ICON[item.level]}
          </span>
          <div>
            <div className="title">{item.title}</div>
            <div className="detail">{item.detail}</div>
            {showTable && (
              <div className="where">
                <code>{item.source_table}</code> → <code>{item.target_table}</code>
              </div>
            )}
          </div>
          <div>
            {item.fix && canEdit && (
              <button className="small" onClick={() => apply(i, item)} disabled={busy !== null}>
                {busy === i ? "Applying…" : `${item.fix.label} & re-run`}
              </button>
            )}
          </div>
        </div>
      ))}
      {limit && items.length > limit && (
        <button className="ghost small" onClick={() => setExpanded(!expanded)}>
          {expanded ? "Show fewer" : `Show all ${items.length} findings`}
        </button>
      )}
    </div>
  );
}
