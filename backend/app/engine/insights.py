"""Turn comparison findings into plain-language insights with suggested fixes.

A fix is machine-applicable when it carries ``fix``:
  {"kind": "option", "path": ["normalization", "case_insensitive"], "value": true, "label": "..."}
  {"kind": "table", "patch": {"exclude_columns": [...]}, "label": "..."}   (applies to this table pair)
The UI applies it to the comparison's config and re-runs.
"""

from __future__ import annotations

from .config import JobOptions

LEVEL_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _n(x: int) -> str:
    return f"{x:,}"


def _insight(level: str, title: str, detail: str, column: str | None = None, fix: dict | None = None) -> dict:
    out = {"level": level, "title": title, "detail": detail}
    if column:
        out["column"] = column
    if fix:
        out["fix"] = fix
    return out


def table_insights(t: dict, options: JobOptions) -> list[dict]:
    """Insights for one table result (as produced by ``compare_table``)."""
    out: list[dict] = []
    norm = options.normalization
    schema = t.get("schema") or {}
    schema_cols = {c["source"]: c for c in schema.get("columns", [])}
    counts = t.get("counts") or {}
    diff = t.get("row_diff")

    # --- schema ------------------------------------------------------------------------
    missing = [m["column"] for m in schema.get("missing_in_target", [])]
    if missing:
        out.append(
            _insight(
                "critical",
                f"{len(missing)} column(s) missing in the target",
                f"{', '.join(missing[:8])}{'…' if len(missing) > 8 else ''} exist in the source but were not found in the "
                "target. Add them to the target table, map them to the right target column in 'Columns & keys', "
                "or exclude them if they were dropped on purpose.",
                fix={"kind": "table", "label": "Exclude these columns", "patch": {"exclude_columns": missing}},
            )
        )
    for c in schema.get("columns", []):
        if c["compatibility"] == "incompatible":
            out.append(
                _insight(
                    "critical",
                    f"{c['source']}: type cannot hold the source data",
                    f"{c['source_type']} → {c['target_type']}: {c['reason']}. Change the target column type.",
                    column=c["source"],
                )
            )

    # --- rows --------------------------------------------------------------------------
    if diff:
        only_s, only_t = diff.get("only_in_source", 0), diff.get("only_in_target", 0)
        if only_s:
            out.append(
                _insight(
                    "critical",
                    f"{_n(only_s)} row(s) missing in the target",
                    "These keys exist in the source but not in the target. Typical causes: a load filter, a failed "
                    "partition or batch, or rows rejected by the target. See 'Data differences' for example keys.",
                )
            )
        if only_t:
            out.append(
                _insight(
                    "warning",
                    f"{_n(only_t)} unexpected row(s) in the target",
                    "These keys exist only in the target: test data, rows loaded twice under new keys, or a "
                    "missing filter on the source side of the comparison.",
                )
            )
        if diff.get("duplicate_keys_target"):
            out.append(
                _insight(
                    "critical",
                    f"{_n(diff['duplicate_keys_target'])} duplicate key(s) in the target",
                    f"The key ({', '.join(diff.get('key_columns', []))}) is not unique in the target: data was "
                    "probably loaded more than once. Matching counts can hide this.",
                )
            )
        if diff.get("duplicate_keys_source"):
            out.append(
                _insight(
                    "warning",
                    f"{_n(diff['duplicate_keys_source'])} duplicate key(s) in the source",
                    "The chosen key is not unique in the source, so rows cannot be matched one-to-one. "
                    "Pick a key that identifies a row (add columns to make a composite key).",
                )
            )
        if diff.get("method") == "row_hash":
            out.append(
                _insight(
                    "info",
                    "Compared without a key",
                    "No key column was configured or detected, so whole rows were compared by hash: a row with "
                    "one changed value counts as missing plus extra. Choose key columns for exact, per-column results.",
                )
            )
        if diff.get("sampled"):
            out.append(
                _insight(
                    "info",
                    "Large table: a sample was compared",
                    f"The first {_n(diff.get('staged_rows', {}).get('source', 0))} source rows by key were compared "
                    "row by row. Raise 'Max rows' to compare more, or use 'Column statistics' mode for full coverage.",
                )
            )
    elif counts and counts.get("source") != counts.get("target"):
        diff_rows = counts.get("target", 0) - counts.get("source", 0)
        out.append(
            _insight(
                "critical" if diff_rows < 0 else "warning",
                f"Row counts differ by {_n(abs(diff_rows))}",
                f"Source has {_n(counts.get('source', 0))} rows, target {_n(counts.get('target', 0))}. "
                "Run a 'Row-level diff' to see which rows.",
            )
        )

    # --- values: explain each column's differences -----------------------------------
    for col in (diff or {}).get("columns", []):
        if not col.get("mismatches"):
            continue
        name = col["source"]
        sc = schema_cols.get(name, {})
        total = col["mismatches"]
        patterns = col.get("patterns") or [{"pattern": "other", "count": total}]
        for k, pat in enumerate(patterns):
            top, count = pat["pattern"], pat["count"]
            if top == "other" and k > 0 and count < 0.1 * total:
                continue  # unexplained leftovers next to a clear main cause
            share = count / total if total else 0
            pct = f"{share:.0%}" if share >= 0.01 else "<1%"
            mostly = "All of" if share == 1 else f"{_n(count)} ({pct}) of"
            gap = float(f"{float(pat.get('max_abs_difference') or 0):.6g}")

            if top == "case_only":
                out.append(
                    _insight(
                        "warning",
                        f"{name}: values differ only in letter case",
                        f"{mostly} the {_n(total)} differences are upper/lower case only (e.g. 'abc' vs 'ABC'). Fix the "
                        "transformation, or ignore case if it does not matter for your users.",
                        column=name,
                        fix=None
                        if norm.case_insensitive
                        else {
                            "kind": "option",
                            "path": ["normalization", "case_insensitive"],
                            "value": True,
                            "label": "Ignore letter case",
                        },
                    )
                )
            elif top == "whitespace_only":
                out.append(
                    _insight(
                        "warning",
                        f"{name}: values differ only in spaces",
                        f"{mostly} the {_n(total)} differences are extra or missing spaces inside the value.",
                        column=name,
                        fix=None
                        if norm.trim_strings
                        else {
                            "kind": "option",
                            "path": ["normalization", "trim_strings"],
                            "value": True,
                            "label": "Ignore leading/trailing spaces",
                        },
                    )
                )
            elif top == "truncated":
                out.append(
                    _insight(
                        "critical",
                        f"{name}: text is cut off in the target",
                        f"{mostly} the {_n(total)} differences are target values that are a shortened copy of the source. "
                        f"The target column ({sc.get('target_type', '?')}) is too short; widen it and reload.",
                        column=name,
                    )
                )
            elif top == "rounding":
                step = gap
                reason = f" ({sc['reason']})" if sc.get("compatibility") == "lossy" else ""
                out.append(
                    _insight(
                        "critical" if share > 0.5 else "warning",
                        f"{name}: numbers are rounded in the target",
                        f"{mostly} the {_n(total)} differences are rounding: the target type {sc.get('target_type', '')} "
                        f"keeps fewer decimals{reason}. Widen the target type to match {sc.get('source_type', 'the source')}, "
                        f"or accept differences up to {step:g} if the precision loss is acceptable.",
                        column=name,
                        fix={
                            "kind": "option",
                            "path": ["normalization", "numeric_tolerance"],
                            "value": float(step),
                            "label": f"Accept differences up to {step:g}",
                        }
                        if step
                        else None,
                    )
                )
            elif top == "precision":
                out.append(
                    _insight(
                        "info",
                        f"{name}: tiny floating-point differences",
                        f"{mostly} the {_n(total)} differences are below one millionth of the value, typical of "
                        "float/double conversion.",
                        column=name,
                        fix={
                            "kind": "option",
                            "path": ["normalization", "numeric_tolerance"],
                            "value": float(gap or 0.000001),
                            "label": "Ignore tiny numeric differences",
                        },
                    )
                )
            elif top == "sign_flip":
                out.append(
                    _insight(
                        "critical",
                        f"{name}: signs are reversed",
                        f"{mostly} the {_n(total)} differences have the same amount with the opposite sign "
                        "(debit/credit logic or an ABS/negation in the pipeline).",
                        column=name,
                    )
                )
            elif top == "timezone_shift":
                out.append(
                    _insight(
                        "critical",
                        f"{name}: timestamps shifted by whole hours",
                        f"{mostly} the {_n(total)} differences are an exact number of hours (or half hours): a time-zone "
                        "conversion. Check the session time zone of the load (e.g. spark.sql.session.timeZone, Snowflake "
                        "TIMEZONE) and whether the column should be stored with or without time zone.",
                        column=name,
                    )
                )
            elif top == "sub_second":
                out.append(
                    _insight(
                        "warning",
                        f"{name}: fractions of a second differ",
                        f"{mostly} the {_n(total)} differences are below one second: the target keeps fewer fractional "
                        "digits.",
                        column=name,
                        fix=None
                        if norm.timestamp_precision == "second"
                        else {
                            "kind": "option",
                            "path": ["normalization", "timestamp_precision"],
                            "value": "second",
                            "label": "Compare timestamps to the second",
                        },
                    )
                )
            elif top == "time_lost":
                out.append(
                    _insight(
                        "critical",
                        f"{name}: time of day was lost",
                        f"{mostly} the {_n(total)} differences keep the date but lose the time (e.g. Oracle DATE, which "
                        "carries a time, loaded into a DATE column). Use a TIMESTAMP column in the target.",
                        column=name,
                    )
                )
            elif top == "null_in_target":
                out.append(
                    _insight(
                        "critical",
                        f"{name}: values became NULL in the target",
                        f"{mostly} the {_n(total)} differences are values that exist in the source but are empty in the "
                        "target: failed parsing or casting, a lookup/join that found nothing, or a column not populated "
                        "by the load.",
                        column=name,
                    )
                )
            elif top == "null_in_source":
                out.append(
                    _insight(
                        "warning",
                        f"{name}: target has values where the source is NULL",
                        f"{mostly} the {_n(total)} differences are defaults filled in by the target (e.g. 0, '' or a "
                        "default date). Confirm this is intended.",
                        column=name,
                        fix=None
                        if norm.null_equals_empty or sc.get("compatibility") != "safe"
                        else {
                            "kind": "option",
                            "path": ["normalization", "null_equals_empty"],
                            "value": True,
                            "label": "Treat empty text as NULL",
                        },
                    )
                )
            else:
                out.append(
                    _insight(
                        "critical" if col.get("mismatch_pct", 0) > 1 else "warning",
                        f"{name}: {_n(total)} value(s) differ",
                        "The values differ in content. Compare the examples in 'Data differences' with the transformation "
                        "logic for this column.",
                        column=name,
                    )
                )

    # --- statistics-only comparison ---------------------------------------------------
    profile = t.get("profile")
    if profile and not diff:
        bad = [c["source"] for c in profile.get("columns", []) if c.get("mismatches")]
        if bad:
            out.append(
                _insight(
                    "warning",
                    f"Column statistics differ for {len(bad)} column(s)",
                    f"{', '.join(bad[:8])}: counts of NULLs, distinct values, min/max or totals differ. Run a "
                    "'Row-level diff' to find the exact rows.",
                )
            )

    # --- quality ------------------------------------------------------------------------
    for q in t.get("quality") or []:
        if q.get("error"):
            out.append(_insight("warning", f"Rule '{q['rule']}' could not run", q["error"]))
        elif not q["passed"]:
            out.append(
                _insight(
                    "critical" if q["severity"] == "high" else "warning",
                    f"Rule '{q['rule']}' failed on the {q['side']}",
                    f"{_n(q['violations'])} of {_n(q['rows_checked'])} rows ({q['violation_pct']}%) break this rule.",
                    column=q.get("column"),
                )
            )

    for i in out:
        i["source_table"], i["target_table"] = t["source_table"], t["target_table"]
    out.sort(key=lambda i: LEVEL_ORDER[i["level"]])
    return out


def error_insight(t: dict) -> dict:
    return {
        "level": "critical",
        "title": "The table could not be compared",
        "detail": t.get("error", "") + (f" {t['hint']}" if t.get("hint") else ""),
        "source_table": t["source_table"],
        "target_table": t["target_table"],
    }
