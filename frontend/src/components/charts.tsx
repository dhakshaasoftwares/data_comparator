import { useRef, useState } from "react";
import { fmtDate, fmtNum } from "../api";

export interface TrendPoint {
  label: string;
  value: number;
  at?: string | null;
  sub?: string;
}

/** Single-series score line (0-100) with pass/warn thresholds, crosshair and tooltip. */
export function ScoreTrend({ points, pass, warn }: { points: TrendPoint[]; pass: number; warn: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const W = 640, H = 220, L = 34, R = 12, T = 12, B = 26;
  const iw = W - L - R, ih = H - T - B;
  const x = (i: number) => L + (points.length <= 1 ? iw / 2 : (i * iw) / (points.length - 1));
  const y = (v: number) => T + ih - (v / 100) * ih;
  const path = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");

  const onMove = (e: React.PointerEvent) => {
    const box = ref.current!.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * W;
    let best = 0;
    points.forEach((_, i) => {
      if (Math.abs(x(i) - px) < Math.abs(x(best) - px)) best = i;
    });
    setHover(best);
  };

  const hp = hover !== null ? points[hover] : null;
  return (
    <div className="chart" ref={ref} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Readiness score per run">
        {[0, 25, 50, 75, 100].map((v) => (
          <g key={v}>
            <line className={v === 0 ? "baseline" : "gridline"} x1={L} x2={W - R} y1={y(v)} y2={y(v)} />
            <text className="tick" x={L - 6} y={y(v) + 4} textAnchor="end">
              {v}
            </text>
          </g>
        ))}
        {[
          [pass, "pass"],
          [warn, "warn"],
        ].map(([v, name]) => (
          <g key={name as string}>
            <line className="threshold" x1={L} x2={W - R} y1={y(v as number)} y2={y(v as number)} />
            <text className="threshold-label" x={L + 6} y={y(v as number) - 4}>
              {name} ≥ {v}
            </text>
          </g>
        ))}
        {hp && <line className="crosshair" x1={x(hover!)} x2={x(hover!)} y1={T} y2={T + ih} />}
        <path className="series" d={path} />
        {points.map((p, i) => (
          <circle key={i} className="dot" cx={x(i)} cy={y(p.value)} r={hover === i ? 5 : points.length > 20 ? 0 : 3.5} />
        ))}
        {points.length > 0 && (
          <text className="tick" x={x(points.length - 1)} y={y(points[points.length - 1].value) - 10} textAnchor="end">
            {points[points.length - 1].value.toFixed(1)}
          </text>
        )}
      </svg>
      {hp && (
        <div
          className="tooltip"
          style={{
            left: `${(x(hover!) / W) * 100}%`,
            top: 0,
            transform: x(hover!) > W * 0.6 ? "translateX(calc(-100% - 12px))" : "translateX(12px)",
          }}
        >
          <strong className="num">{hp.value.toFixed(1)}</strong>
          <span className="ink-2">{hp.label}</span>
          {hp.sub && <div className="muted">{hp.sub}</div>}
          {hp.at && <div className="muted">{fmtDate(hp.at)}</div>}
        </div>
      )}
    </div>
  );
}

/** Horizontal single-series bars; values are always printed so nothing depends on hover. */
export function BarList({
  rows,
  format = fmtNum,
  max,
}: {
  rows: { label: string; value: number; title?: string }[];
  format?: (n: number) => string;
  max?: number;
}) {
  const top = max ?? Math.max(1, ...rows.map((r) => r.value));
  return (
    <div role="list">
      {rows.map((r) => (
        <div className="bar-row" role="listitem" key={r.label} title={r.title || `${r.label}: ${format(r.value)}`}>
          <span className="name">{r.label}</span>
          <span className="track">
            <span className="fill" style={{ display: "block", width: `${Math.max(0, (r.value / top) * 100)}%` }} />
          </span>
          <span className="num r" style={{ textAlign: "right" }}>
            {format(r.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

const STATUS_SEGMENTS: { key: string; label: string; color: string; ico: string }[] = [
  { key: "pass", label: "Pass", color: "var(--good)", ico: "✓" },
  { key: "warn", label: "Warning", color: "var(--warning)", ico: "!" },
  { key: "fail", label: "Fail", color: "var(--critical)", ico: "✕" },
  { key: "error", label: "Error", color: "var(--muted)", ico: "✕" },
];

/** Share of tables per status as one stacked bar with a labelled legend. */
export function StatusStack({ counts }: { counts: Record<string, number> }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  if (!total) return <div className="muted">No tables compared yet.</div>;
  return (
    <div>
      <div className="stackbar" role="img" aria-label="Tables by status">
        {STATUS_SEGMENTS.filter((s) => counts[s.key]).map((s) => (
          <div
            key={s.key}
            title={`${s.label}: ${counts[s.key]}`}
            style={{ width: `${(counts[s.key] / total) * 100}%`, background: s.color }}
          />
        ))}
      </div>
      <div className="legend">
        {STATUS_SEGMENTS.map((s) => (
          <span key={s.key}>
            <span className="key" style={{ background: s.color }} />
            {s.ico} {s.label} <b className="num">{counts[s.key] || 0}</b>
          </span>
        ))}
      </div>
    </div>
  );
}
