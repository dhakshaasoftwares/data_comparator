"""Data quality rules evaluated on staged data inside the sandboxed DuckDB session."""

from __future__ import annotations

import duckdb

from .config import QualityRule
from .data_compare import jsonable
from .staging import qi, ql

SEVERITY_WEIGHT = {"low": 1, "medium": 2, "high": 3}


def _valid_condition(rule: QualityRule, column: str | None) -> str:
    c = qi(column) if column else None
    t = rule.type
    if t == "not_null":
        return f"{c} IS NOT NULL"
    if t == "range":
        num = f"TRY_CAST({c} AS DOUBLE)"
        conds = []
        if rule.min is not None:
            conds.append(f"{num} >= {float(rule.min)!r}")
        if rule.max is not None:
            conds.append(f"{num} <= {float(rule.max)!r}")
        return f"({c} IS NULL OR ({' AND '.join(conds) or 'TRUE'}))"
    if t == "regex":
        return f"({c} IS NULL OR regexp_full_match(CAST({c} AS VARCHAR), {ql(rule.pattern or '')}))"
    if t == "allowed_values":
        values = ", ".join(ql(str(v)) for v in rule.values) or "NULL"
        return f"({c} IS NULL OR CAST({c} AS VARCHAR) IN ({values}))"
    if t == "max_length":
        return f"({c} IS NULL OR length(CAST({c} AS VARCHAR)) <= {int(rule.length or 0)})"
    if t == "expression":
        expr = (rule.expression or "").strip().rstrip(";")
        if ";" in expr:
            raise ValueError("Expression must be a single boolean SQL expression")
        return f"COALESCE(({expr}), FALSE)"
    raise ValueError(f"Unsupported rule type {t}")


def resolve_column(name: str | None, side: str, columns: set[str], mapping: dict[str, str]) -> str | None:
    """Rule columns may use source or target names; resolve them for ``side``."""
    if name is None:
        return None
    if name in columns:
        return name
    if side == "target" and name in mapping:
        return mapping[name]
    reverse = {v: k for k, v in mapping.items()}
    if side == "source" and name in reverse:
        return reverse[name]
    lowered = {c.lower(): c for c in columns}
    if name.lower() in lowered:
        return lowered[name.lower()]
    raise ValueError(f"Column '{name}' not found on {side}")


def run_rule(
    duck: duckdb.DuckDBPyConnection,
    table: str,
    side: str,
    rule: QualityRule,
    columns: set[str],
    mapping: dict[str, str],
    sample_size: int = 5,
) -> dict:
    result = {
        "rule": rule.label(),
        "type": rule.type,
        "side": side,
        "severity": rule.severity,
        "column": rule.column,
        "threshold_pct": rule.max_violation_pct,
    }
    try:
        total = int(duck.execute(f"SELECT count(*) FROM {qi(table)}").fetchone()[0])
        if rule.type == "unique":
            cols = [resolve_column(c, side, columns, mapping) for c in (rule.columns or [rule.column])]
            cl = ", ".join(qi(c) for c in cols)
            violations = int(
                duck.execute(
                    f"SELECT coalesce(sum(n), 0) FROM "
                    f"(SELECT count(*) AS n FROM {qi(table)} GROUP BY {cl} HAVING count(*) > 1)"
                ).fetchone()[0]
            )
            rows = duck.execute(
                f"SELECT {cl}, count(*) AS occurrences FROM {qi(table)} GROUP BY {cl} HAVING count(*) > 1 "
                f"ORDER BY occurrences DESC LIMIT {int(sample_size)}"
            ).fetchall()
            names = cols + ["occurrences"]
        else:
            col = resolve_column(rule.column, side, columns, mapping)
            cond = _valid_condition(rule, col)
            violations = int(duck.execute(f"SELECT count(*) FROM {qi(table)} WHERE NOT ({cond})").fetchone()[0])
            cur = duck.execute(f"SELECT * FROM {qi(table)} WHERE NOT ({cond}) LIMIT {int(sample_size)}")
            names = [d[0] for d in cur.description]
            rows = cur.fetchall()
            if col:  # keep samples readable: the checked column first, then a few others
                order = [col] + [n for n in names if n != col][:7]
                idx = [names.index(n) for n in order]
                names, rows = order, [tuple(r[i] for i in idx) for r in rows]
        pct = 100.0 * violations / total if total else 0.0
        result.update(
            {
                "rows_checked": total,
                "violations": violations,
                "violation_pct": round(pct, 4),
                "passed": pct <= rule.max_violation_pct,
                "samples": [{n: jsonable(v) for n, v in zip(names, r)} for r in rows],
            }
        )
    except Exception as exc:  # bad column, bad expression...
        result.update(
            {
                "rows_checked": 0,
                "violations": None,
                "violation_pct": None,
                "passed": False,
                "error": str(exc).splitlines()[0][:300],
                "samples": [],
            }
        )
    return result
