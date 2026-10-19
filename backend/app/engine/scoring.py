"""Migration readiness score: a weighted, explainable 0-100 number per table."""

from __future__ import annotations

from .config import JobOptions
from .quality import SEVERITY_WEIGHT


def status_for(score: float | None, options: JobOptions) -> str:
    if score is None:
        return "error"
    if score >= options.thresholds.pass_score:
        return "pass"
    if score >= options.thresholds.warn_score:
        return "warn"
    return "fail"


def _ratio(a: int, b: int) -> float:
    if a == b:
        return 1.0
    return min(a, b) / max(a, b) if max(a, b) else 1.0


def score_table(result: dict, options: JobOptions) -> dict:
    """Compute component scores (0..1) and the weighted total with deductions."""
    counts = result["counts"]
    schema = result["schema"]
    diff = result.get("row_diff")
    profile = result.get("profile")
    quality = result.get("quality") or []
    comps: dict[str, float | None] = {}
    details: dict[str, str] = {}

    # Completeness: do the same rows exist on both sides?
    count_ratio = _ratio(counts["source"], counts["target"])
    comps["completeness"] = count_ratio
    details["completeness"] = f"Row counts {counts['source']:,} vs {counts['target']:,}"
    if diff:
        universe = diff["matched_rows"] + diff["only_in_source"] + diff["only_in_target"]
        key_ratio = diff["matched_rows"] / universe if universe else 1.0
        comps["completeness"] = min(count_ratio, key_ratio) if diff.get("sampled") else key_ratio
        details["completeness"] = (
            f"{diff['only_in_source']:,} rows missing in target, {diff['only_in_target']:,} unexpected rows in target"
        )

    # Accuracy: do matched rows carry the same values?
    comps["accuracy"] = None
    if diff and diff["method"] == "key":
        if diff["matched_rows"]:
            comps["accuracy"] = diff["identical_rows"] / diff["matched_rows"]
            worst = sorted((c for c in diff["columns"] if c["mismatches"]), key=lambda c: -c["mismatches"])[:3]
            cols = ", ".join(f"{c['source']} ({c['mismatches']:,})" for c in worst)
            details["accuracy"] = f"{diff['mismatched_rows']:,} matched rows have different values" + (
                f"; worst columns: {cols}" if cols else ""
            )
        else:
            comps["accuracy"] = 0.0 if (counts["source"] or counts["target"]) else 1.0
            details["accuracy"] = "No rows could be matched by key"
    elif diff and diff["method"] == "row_hash":
        total = max(counts["source"], counts["target"], diff["matched_rows"] + diff["only_in_source"])
        comps["accuracy"] = diff["matched_rows"] / total if total else 1.0
        details["accuracy"] = f"{diff['matched_rows']:,} identical rows (row hash, no key)"
    elif profile and profile["checks"]:
        comps["accuracy"] = profile["passed"] / profile["checks"]
        details["accuracy"] = f"{profile['checks'] - profile['passed']} of {profile['checks']} column statistics differ"

    # Schema compatibility
    comps["schema"] = schema["score"]
    s = schema["summary"]
    details["schema"] = (
        f"{s['lossy']} lossy, {s['incompatible']} incompatible, {s['missing_in_target']} missing column(s)"
    )

    # Data quality
    comps["quality"] = None
    if quality:
        total_w = sum(SEVERITY_WEIGHT[q["severity"]] for q in quality)
        ok_w = sum(SEVERITY_WEIGHT[q["severity"]] for q in quality if q["passed"])
        comps["quality"] = ok_w / total_w if total_w else 1.0
        failed = [q["rule"] for q in quality if not q["passed"]]
        details["quality"] = f"{len(failed)} of {len(quality)} rules failed" + (
            f": {', '.join(failed[:4])}" if failed else ""
        )

    w = options.weights
    weights = {"completeness": w.completeness, "accuracy": w.accuracy, "schema": w.schema_, "quality": w.quality}
    active = {k: weights[k] for k, v in comps.items() if v is not None and weights[k] > 0}
    total_w = sum(active.values()) or 1.0
    score = 100.0 * sum(comps[k] * wt for k, wt in active.items()) / total_w

    components, deductions = [], []
    for k in ("completeness", "accuracy", "schema", "quality"):
        v = comps[k]
        share = 100.0 * active.get(k, 0) / total_w
        components.append(
            {
                "name": k,
                "value": None if v is None else round(100 * v, 2),
                "weight": round(share, 2),
                "applied": k in active,
                "detail": details.get(k, "Not measured in this mode"),
            }
        )
        if k in active and v < 1:
            deductions.append({"component": k, "points": round(share * (1 - v), 2), "reason": details[k]})
    deductions.sort(key=lambda d: -d["points"])
    final = round(score, 2)
    return {"score": final, "status": status_for(final, options), "components": components, "deductions": deductions}
