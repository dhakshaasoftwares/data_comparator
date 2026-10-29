"""Runs a comparison job: schema -> counts -> profile / row diff -> quality -> score."""

from __future__ import annotations

import datetime as dt
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

from ..connectors import Connector, sandboxed_duckdb
from ..connectors.errors import explain
from ..settings import get_settings
from .config import JobConfig, JobOptions, TablePair
from .data_compare import ColumnPlan, compare_profiles, keyed_diff, keyless_diff, profileable, staged_profile
from .insights import LEVEL_ORDER, error_insight, table_insights
from .keys import detect_unique_key
from .quality import run_rule
from .schema_compare import compare_schema
from .scoring import score_table, status_for
from .staging import qi
from .types import NUMERIC_FAMILIES

Progress = Callable[[int, int, str], None]


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def compare_table(pair: TablePair, src: Connector, tgt: Connector, options: JobOptions) -> dict:
    t0 = time.monotonic()
    notes: list[str] = []
    result: dict = {"source_table": pair.source_table, "target_table": pair.target_table, "notes": notes}

    src_cols = src.get_columns(pair.source_table)
    tgt_cols = tgt.get_columns(pair.target_table)
    schema = compare_schema(src_cols, tgt_cols, src.dialect, tgt.dialect, pair.column_mappings, pair.exclude_columns)
    result["schema"] = schema.to_dict()
    mapping = {c.source: c.target for c in schema.columns}

    counts = {
        "source": src.count_rows(pair.source_table, pair.source_filter),
        "target": tgt.count_rows(pair.target_table, pair.target_filter),
    }
    counts["difference"] = counts["target"] - counts["source"]
    result["counts"] = counts

    keys = list(pair.key_columns)
    result["key_source"] = "configured"
    if keys:
        missing = [k for k in keys if k not in mapping]
        if missing:
            raise ValueError(
                f"Key column(s) {', '.join(missing)} are not mapped to the target. "
                "Map them in 'Columns & keys' or choose other key columns."
            )
    else:
        keys = [k for k in src.get_primary_key(pair.source_table) if k in mapping]
        result["key_source"] = "primary_key"
        if keys:
            notes.append(f"Using the source primary key: {', '.join(keys)}")
        elif options.mode == "full":
            keys = detect_unique_key(
                src, pair.source_table, list(mapping), schema.source_types, pair.source_filter, counts["source"]
            )
            result["key_source"] = "detected"
            if keys:
                notes.append(f"No primary key declared; detected '{keys[0]}' as a unique key in the source")
        if not keys:
            result["key_source"] = "none"
    result["key_columns"] = keys

    plans = [
        ColumnPlan(
            c.source, c.target, schema.source_types[c.source], schema.target_types[c.target], options.normalization
        )
        for c in schema.columns
    ]
    profile_plans = [p for p in plans if profileable(p.s_type) and profileable(p.t_type)]

    def numeric(p: ColumnPlan) -> bool:
        return p.s_type.family in NUMERIC_FAMILIES and p.t_type.family in NUMERIC_FAMILIES

    limit = options.max_rows
    do_diff = options.mode == "full" and bool(plans)
    if do_diff and not keys and limit and max(counts["source"], counts["target"]) > limit:
        do_diff = False
        notes.append(
            f"No key columns and more than {limit:,} rows: row-level diff skipped, "
            "column statistics compared in-engine instead"
        )

    tol = options.normalization.numeric_tolerance
    if options.mode == "profile" or (options.mode == "full" and not do_diff):
        sp = src.profile(pair.source_table, [(p.source, numeric(p)) for p in profile_plans], pair.source_filter)
        tp = tgt.profile(pair.target_table, [(p.target, numeric(p)) for p in profile_plans], pair.target_filter)
        result["profile"] = {**compare_profiles(profile_plans, sp, tp, tol), "computed": "pushdown"}

    rule_sides = {s for r in pair.rules for s in (("source", "target") if r.side == "both" else (r.side,))}
    if not do_diff and not rule_sides:
        result["duration_seconds"] = round(time.monotonic() - t0, 3)
        return result

    settings = get_settings()
    db_path = Path(settings.work_dir) / f"stage-{uuid.uuid4().hex}.duckdb"
    duck = sandboxed_duckdb([src, tgt], str(db_path))
    try:
        src_names = [c.name for c in src_cols if c.name.lower() not in {e.lower() for e in pair.exclude_columns}]
        tgt_names = [c.name for c in tgt_cols]
        staged = {}
        if do_diff:
            sampled = bool(limit) and max(counts["source"], counts["target"]) > limit
            if sampled and keys:
                # Take the first `limit` source rows in key order, then the target rows in
                # the same key range, so both windows describe the same records.
                staged["source"] = src.stage(
                    duck, "src", pair.source_table, src_names, pair.source_filter, limit=limit, order_by=keys
                )
                k0 = keys[0]
                bound = duck.execute(f"SELECT max({qi(k0)}) FROM src").fetchone()[0]
                inclusive = True
                if staged["source"] >= limit:
                    below = duck.execute(f"SELECT count(*) FROM src WHERE {qi(k0)} < ?", [bound]).fetchone()[0]
                    if below:  # drop the possibly incomplete last key group
                        duck.execute(f"DELETE FROM src WHERE {qi(k0)} >= ?", [bound])
                        staged["source"] = int(below)
                        inclusive = False
                t_limit = limit * 2
                staged["target"] = tgt.stage(
                    duck,
                    "tgt",
                    pair.target_table,
                    tgt_names,
                    pair.target_filter,
                    limit=t_limit,
                    order_by=[mapping[k] for k in keys],
                    upper_bound=(mapping[k0], bound, inclusive),
                )
                if staged["target"] >= t_limit:
                    notes.append("Target window was truncated; extra target rows may be under-reported")
                notes.append(
                    f"Sampled comparison: first {staged['source']:,} source rows by key (max_rows = {limit:,})"
                )
            else:
                staged["source"] = src.stage(duck, "src", pair.source_table, src_names, pair.source_filter)
                staged["target"] = tgt.stage(duck, "tgt", pair.target_table, tgt_names, pair.target_filter)
            key_plans = [p for p in plans if p.source in keys]
            diff = (
                keyed_diff(duck, plans, key_plans, options.sample_size)
                if key_plans
                else keyless_diff(duck, plans, options.sample_size)
            )
            diff["sampled"] = sampled
            diff["staged_rows"] = staged
            result["row_diff"] = diff
            sp = staged_profile(duck, "src", [(p.source, numeric(p)) for p in profile_plans])
            tp = staged_profile(duck, "tgt", [(p.target, numeric(p)) for p in profile_plans])
            result["profile"] = {
                **compare_profiles(profile_plans, sp, tp, tol),
                "computed": "staged sample" if sampled else "staged",
            }
        else:
            if "source" in rule_sides:
                staged["source"] = src.stage(duck, "src", pair.source_table, src_names, pair.source_filter, limit=limit)
            if "target" in rule_sides:
                staged["target"] = tgt.stage(duck, "tgt", pair.target_table, tgt_names, pair.target_filter, limit=limit)
            for side, n in staged.items():
                if limit and n >= limit:
                    notes.append(f"Quality rules on {side} evaluated on the first {limit:,} rows")

        quality = []
        for rule in pair.rules:
            for side in ("source", "target") if rule.side == "both" else (rule.side,):
                table, cols = ("src", set(src_names)) if side == "source" else ("tgt", set(tgt_names))
                quality.append(run_rule(duck, table, side, rule, cols, mapping))
        if quality:
            result["quality"] = quality
    finally:
        duck.close()
        for suffix in ("", ".wal"):
            with suppress(OSError):
                Path(str(db_path) + suffix).unlink()

    result["duration_seconds"] = round(time.monotonic() - t0, 3)
    return result


def run_job(config: JobConfig, src: Connector, tgt: Connector, progress: Progress | None = None) -> dict:
    options = config.options
    started = _now()
    tables = []
    total = len(config.tables)
    for i, pair in enumerate(config.tables):
        if progress:
            progress(i, total, pair.source_table)
        try:
            table = compare_table(pair, src, tgt, options)
            table.update(score_table(table, options))
            table["insights"] = table_insights(table, options)
        except Exception as exc:  # one broken table must not stop the others
            message = str(exc).splitlines()[0][:500] if str(exc) else type(exc).__name__
            table = {
                "source_table": pair.source_table,
                "target_table": pair.target_table,
                "status": "error",
                "score": None,
                "error": message,
                "hint": explain(message),
            }
            table["insights"] = [error_insight(table)]
        tables.append(table)
    if progress:
        progress(total, total, "")

    scores = [t["score"] for t in tables if t.get("score") is not None]
    overall = round(sum(scores) / len(scores), 2) if scores else None
    status = status_for(overall, options)
    errors = sum(1 for t in tables if t["status"] == "error")
    if errors and status == "pass":
        status = "warn"
    return {
        "started_at": started,
        "finished_at": _now(),
        "mode": options.mode,
        "summary": {
            "score": overall,
            "status": status,
            "tables": total,
            "passed": sum(1 for t in tables if t["status"] == "pass"),
            "warned": sum(1 for t in tables if t["status"] == "warn"),
            "failed": sum(1 for t in tables if t["status"] == "fail"),
            "errors": errors,
            "source_rows": sum(t.get("counts", {}).get("source", 0) for t in tables),
            "target_rows": sum(t.get("counts", {}).get("target", 0) for t in tables),
        },
        "insights": sorted(
            (i for t in tables for i in t["insights"] if i["level"] != "info"),
            key=lambda i: LEVEL_ORDER[i["level"]],
        )[:25],
        "tables": tables,
    }
