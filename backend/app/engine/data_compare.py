"""Row-level and statistical comparison of staged data (DuckDB)."""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from typing import Any

import duckdb

from .config import Normalization
from .staging import qi
from .types import NUMERIC_FAMILIES, CanonicalType, Family, comparison_family

MAX_SAMPLE_COLUMNS = 40


def jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray, memoryview)):
        return "0x" + bytes(v).hex()[:64]
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    return str(v)


class ColumnPlan:
    """How one mapped column pair is normalised and compared."""

    def __init__(self, source: str, target: str, s_type: CanonicalType, t_type: CanonicalType, norm: Normalization):
        self.source, self.target = source, target
        self.s_type, self.t_type = s_type, t_type
        self.family = comparison_family(s_type, t_type)
        self.norm = norm
        self.decimal_type = "DOUBLE"
        self.float_like = Family.FLOAT in (s_type.family, t_type.family) or (
            Family.STRING in (s_type.family, t_type.family) and self.family == Family.DECIMAL
        )
        if self.family == Family.DECIMAL and not self.float_like:
            exact = [t for t in (s_type, t_type) if t.family in (Family.DECIMAL, Family.INTEGER)]
            scales = [t.scale if t.family == Family.DECIMAL else 0 for t in exact]
            ints = [((t.precision or 0) - (t.scale or 0)) if t.family == Family.DECIMAL else 19 for t in exact]
            if all(s is not None for s in scales) and all(t.precision or t.family == Family.INTEGER for t in exact):
                scale = max(scales)
                if max(ints) + scale <= 38:
                    self.decimal_type = f"DECIMAL(38,{scale})"

    def expr(self, col: str, ct: CanonicalType) -> str:
        c = qi(col)
        f = self.family
        n = self.norm
        if f == Family.STRING:
            e = f"CAST({c} AS VARCHAR)"
            if n.trim_strings:
                e = f"trim({e})"
            if n.case_insensitive:
                e = f"lower({e})"
            if n.null_equals_empty:
                e = f"NULLIF({e}, '')"
            return e
        if f == Family.INTEGER:
            if ct.family == Family.BOOLEAN:
                return f"CAST({c} AS INTEGER)"
            return f"TRY_CAST({c} AS HUGEINT)"
        if f == Family.DECIMAL:
            src = f"trim(CAST({c} AS VARCHAR))" if ct.family == Family.STRING else c
            return f"TRY_CAST({src} AS {self.decimal_type})"
        if f == Family.TIMESTAMP:
            e = f"TRY_CAST({c} AS TIMESTAMP)"
            if n.timestamp_precision != "microsecond":
                e = f"date_trunc('{n.timestamp_precision}', {e})"
            return e
        if f == Family.DATE:
            return f"TRY_CAST({c} AS DATE)"
        if f == Family.BOOLEAN:
            return f"TRY_CAST({c} AS BOOLEAN)"
        if f == Family.BINARY:
            return c
        return f"CAST({c} AS VARCHAR)"

    def source_expr(self) -> str:
        return self.expr(self.source, self.s_type)

    def target_expr(self) -> str:
        return self.expr(self.target, self.t_type)

    def equal(self, a: str, b: str) -> str:
        exact = f"({a} IS NOT DISTINCT FROM {b})"
        if self.family not in NUMERIC_FAMILIES:
            return exact
        tol = self.norm.numeric_tolerance or 0.0
        da, db = f"CAST({a} AS DOUBLE)", f"CAST({b} AS DOUBLE)"
        conds = [exact]
        if tol > 0:
            conds.append(f"abs({da} - {db}) <= {float(tol)!r}")
        if self.float_like:
            conds.append(f"abs({da} - {db}) <= 1e-6 * greatest(abs({da}), abs({db}))")
        return "(" + " OR ".join(conds) + ")"


def _count(duck, sql: str) -> int:
    return int(duck.execute(sql).fetchone()[0] or 0)


def keyed_diff(
    duck: duckdb.DuckDBPyConnection, plans: list[ColumnPlan], keys: list[ColumnPlan], sample_size: int
) -> dict:
    """Full outer join on the key columns of staged tables ``src`` and ``tgt``."""
    non_keys = [p for p in plans if p not in keys]
    s_sel = [f"{k.source_expr()} AS k{i}" for i, k in enumerate(keys)]
    s_sel += [f"{p.source_expr()} AS c{i}" for i, p in enumerate(non_keys)]
    t_sel = [f"{k.target_expr()} AS k{i}" for i, k in enumerate(keys)]
    t_sel += [f"{p.target_expr()} AS c{i}" for i, p in enumerate(non_keys)]
    duck.execute(f"CREATE OR REPLACE TEMP TABLE s_norm AS SELECT {', '.join(s_sel)} FROM src")
    duck.execute(f"CREATE OR REPLACE TEMP TABLE t_norm AS SELECT {', '.join(t_sel)} FROM tgt")

    key_list = ", ".join(f"k{i}" for i in range(len(keys)))
    dup_s = _count(
        duck, f"SELECT count(*) - (SELECT count(*) FROM (SELECT DISTINCT {key_list} FROM s_norm)) FROM s_norm"
    )
    dup_t = _count(
        duck, f"SELECT count(*) - (SELECT count(*) FROM (SELECT DISTINCT {key_list} FROM t_norm)) FROM t_norm"
    )
    null_keys_s = _count(
        duck, "SELECT count(*) FROM s_norm WHERE " + " OR ".join(f"k{i} IS NULL" for i in range(len(keys)))
    )
    null_keys_t = _count(
        duck, "SELECT count(*) FROM t_norm WHERE " + " OR ".join(f"k{i} IS NULL" for i in range(len(keys)))
    )

    on = " AND ".join(f"s.k{i} = t.k{i}" for i in range(len(keys)))
    cols = [f"s.k{i} AS sk{i}, t.k{i} AS tk{i}" for i in range(len(keys))]
    cols += ["s.k0 IS NOT NULL AS sp", "t.k0 IS NOT NULL AS tp"]
    for i, p in enumerate(non_keys):
        cols.append(f"s.c{i} AS sc{i}, t.c{i} AS tc{i}")
        cols.append(
            f"CASE WHEN s.k0 IS NOT NULL AND t.k0 IS NOT NULL AND NOT {p.equal(f's.c{i}', f't.c{i}')} "
            f"THEN 1 ELSE 0 END AS m{i}"
        )
    # Rows with NULL keys cannot be matched; they are excluded and reported separately.
    s_where = " AND ".join(f"k{i} IS NOT NULL" for i in range(len(keys)))
    duck.execute(
        f"CREATE OR REPLACE TEMP TABLE diff AS SELECT {', '.join(cols)} "
        f"FROM (SELECT * FROM s_norm WHERE {s_where}) s "
        f"FULL OUTER JOIN (SELECT * FROM t_norm WHERE {s_where}) t ON {on}"
    )

    any_mismatch = " + ".join(f"m{i}" for i in range(len(non_keys))) or "0"
    agg = [
        "count(*) FILTER (WHERE sp AND tp)",
        "count(*) FILTER (WHERE sp AND NOT tp)",
        "count(*) FILTER (WHERE tp AND NOT sp)",
        f"count(*) FILTER (WHERE sp AND tp AND ({any_mismatch}) > 0)",
    ] + [f"sum(m{i})" for i in range(len(non_keys))]
    row = duck.execute(f"SELECT {', '.join(agg)} FROM diff").fetchone()
    matched, only_s, only_t, mismatched = (int(x or 0) for x in row[:4])
    per_col = [int(x or 0) for x in row[4:]]

    columns = []
    for i, p in enumerate(non_keys):
        columns.append(
            {
                "source": p.source,
                "target": p.target,
                "compared_as": p.family.value,
                "mismatches": per_col[i],
                "mismatch_pct": round(100.0 * per_col[i] / matched, 4) if matched else 0.0,
            }
        )

    key_names = [k.source for k in keys]
    sample_cols = non_keys[:MAX_SAMPLE_COLUMNS]

    def key_rows(side: str, cond: str) -> list[dict]:
        sel = ", ".join(f"{side}k{i}" for i in range(len(keys)))
        rows = duck.execute(f"SELECT {sel} FROM diff WHERE {cond} LIMIT {int(sample_size)}").fetchall()
        return [{k: jsonable(v) for k, v in zip(key_names, r)} for r in rows]

    samples_only_s = key_rows("s", "sp AND NOT tp")
    samples_only_t = key_rows("t", "tp AND NOT sp")

    mismatch_samples = []
    if mismatched:
        sel = [f"sk{i}" for i in range(len(keys))]
        idx = [i for i, _ in enumerate(non_keys) if per_col[i] and non_keys[i] in sample_cols]
        for i in idx:
            sel += [f"sc{i}", f"tc{i}", f"m{i}"]
        rows = duck.execute(
            f"SELECT {', '.join(sel)} FROM diff WHERE sp AND tp AND ({any_mismatch}) > 0 LIMIT {int(sample_size)}"
        ).fetchall()
        for r in rows:
            key = {k: jsonable(v) for k, v in zip(key_names, r[: len(keys)])}
            diffs = []
            for j, i in enumerate(idx):
                sv, tv, m = r[len(keys) + 3 * j : len(keys) + 3 * j + 3]
                if m:
                    diffs.append(
                        {
                            "column": non_keys[i].source,
                            "target_column": non_keys[i].target,
                            "source": jsonable(sv),
                            "target": jsonable(tv),
                        }
                    )
            mismatch_samples.append({"key": key, "differences": diffs})

    return {
        "method": "key",
        "key_columns": key_names,
        "matched_rows": matched,
        "only_in_source": only_s,
        "only_in_target": only_t,
        "mismatched_rows": mismatched,
        "identical_rows": matched - mismatched,
        "duplicate_keys_source": dup_s,
        "duplicate_keys_target": dup_t,
        "null_keys_source": null_keys_s,
        "null_keys_target": null_keys_t,
        "columns": columns,
        "samples": {
            "only_in_source": samples_only_s,
            "only_in_target": samples_only_t,
            "mismatches": mismatch_samples,
        },
    }


def keyless_diff(duck: duckdb.DuckDBPyConnection, plans: list[ColumnPlan], sample_size: int) -> dict:
    """Multiset comparison of whole rows using a row hash (no key available)."""
    s_cols = [f"{p.source_expr()} AS c{i}" for i, p in enumerate(plans)]
    t_cols = [f"{p.target_expr()} AS c{i}" for i, p in enumerate(plans)]
    c_list = ", ".join(f"c{i}" for i in range(len(plans)))
    for name, cols, staged in (("s_norm", s_cols, "src"), ("t_norm", t_cols, "tgt")):
        duck.execute(
            f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT *, hash({c_list}) AS h "
            f"FROM (SELECT {', '.join(cols)} FROM {staged})"
        )
    duck.execute(
        "CREATE OR REPLACE TEMP TABLE hdiff AS "
        "SELECT coalesce(s.h, t.h) AS h, coalesce(s.n, 0) AS sn, coalesce(t.n, 0) AS tn "
        "FROM (SELECT h, count(*) n FROM s_norm GROUP BY h) s "
        "FULL OUTER JOIN (SELECT h, count(*) n FROM t_norm GROUP BY h) t ON s.h = t.h"
    )
    matched, only_s, only_t = (
        int(x or 0)
        for x in duck.execute(
            "SELECT sum(least(sn, tn)), sum(greatest(sn - tn, 0)), sum(greatest(tn - sn, 0)) FROM hdiff"
        ).fetchone()
    )
    names_s = [p.source for p in plans][:MAX_SAMPLE_COLUMNS]
    names_t = [p.target for p in plans][:MAX_SAMPLE_COLUMNS]
    sel = ", ".join(f"c{i}" for i in range(len(names_s)))

    def rows(table: str, cond: str, names: list[str]) -> list[dict]:
        r = duck.execute(
            f"SELECT {sel} FROM {table} WHERE h IN (SELECT h FROM hdiff WHERE {cond}) LIMIT {int(sample_size)}"
        ).fetchall()
        return [{n: jsonable(v) for n, v in zip(names, row)} for row in r]

    return {
        "method": "row_hash",
        "key_columns": [],
        "matched_rows": matched,
        "only_in_source": only_s,
        "only_in_target": only_t,
        "mismatched_rows": 0,
        "identical_rows": matched,
        "columns": [],
        "samples": {
            "only_in_source": rows("s_norm", "sn > tn", names_s),
            "only_in_target": rows("t_norm", "tn > sn", names_t),
            "mismatches": [],
        },
    }


# ---------------------------------------------------------------------------
# Profile (pushdown statistics) comparison
# ---------------------------------------------------------------------------


def _to_number(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_ts(v):
    if isinstance(v, str):
        try:
            v = dt.datetime.fromisoformat(v)
        except ValueError:
            return v
    if isinstance(v, dt.datetime):
        if v.tzinfo is not None:
            v = v.astimezone(dt.UTC).replace(tzinfo=None)
        return v
    if isinstance(v, dt.date):
        return dt.datetime(v.year, v.month, v.day)
    return v


def _values_equal(a, b, family: Family, tol: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if family in NUMERIC_FAMILIES or family == Family.BOOLEAN:
        x, y = _to_number(a), _to_number(b)
        if x is None or y is None:
            return str(a) == str(b)
        return abs(x - y) <= max(tol, 1e-9 * max(abs(x), abs(y), 1.0))
    if family in (Family.TIMESTAMP, Family.DATE):
        return _to_ts(a) == _to_ts(b)
    return str(a).strip() == str(b).strip()


PROFILE_FAMILIES = NUMERIC_FAMILIES | {Family.STRING, Family.DATE, Family.TIMESTAMP, Family.BOOLEAN}


def profileable(ct: CanonicalType) -> bool:
    if ct.family == Family.STRING and ct.length is None and ct.native.upper() in ("CLOB", "NCLOB", "TEXT", "NTEXT"):
        return False
    return ct.family in PROFILE_FAMILIES


def compare_profiles(plans: list[ColumnPlan], src: dict[str, dict], tgt: dict[str, dict], tol: float) -> dict:
    columns = []
    checks = passed = 0
    for p in plans:
        s, t = src.get(p.source, {}), tgt.get(p.target, {})
        entry = {"source": p.source, "target": p.target, "compared_as": p.family.value, "metrics": []}
        if "error" in s or "error" in t:
            entry["error"] = s.get("error") or t.get("error")
            columns.append(entry)
            continue
        metrics = ["nulls", "distinct", "min", "max"] + (["sum"] if p.family in NUMERIC_FAMILIES else [])
        for m in metrics:
            sv, tv = s.get(m), t.get(m)
            if m in ("nulls", "distinct"):
                eq = _values_equal(sv, tv, Family.INTEGER, 0)
            elif m == "sum":
                eq = _values_equal(sv, tv, Family.DECIMAL, tol)
            else:
                eq = _values_equal(sv, tv, p.family, tol)
            # String min/max depend on engine collation; shown but not scored.
            scored = not (m in ("min", "max") and p.family == Family.STRING)
            if scored:
                checks += 1
                passed += int(eq)
            entry["metrics"].append(
                {"metric": m, "source": jsonable(sv), "target": jsonable(tv), "match": eq, "scored": scored}
            )
        entry["mismatches"] = sum(1 for m in entry["metrics"] if not m["match"] and m["scored"])
        columns.append(entry)
    return {"columns": columns, "checks": checks, "passed": passed}


def staged_profile(duck: duckdb.DuckDBPyConnection, table: str, columns: list[tuple[str, bool]]) -> dict[str, dict]:
    """Same statistics as Connector.profile, computed on a staged table."""
    if not columns:
        return {}
    parts = []
    for name, numeric in columns:
        c = qi(name)
        parts += [f"count(*) - count({c})", f"count(DISTINCT {c})", f"min({c})", f"max({c})"]
        parts.append(f"sum({c})" if numeric else "NULL")
    row = duck.execute(f"SELECT {', '.join(parts)} FROM {qi(table)}").fetchone()
    out = {}
    for i, (name, numeric) in enumerate(columns):
        n, d, mn, mx, s = row[i * 5 : i * 5 + 5]
        out[name] = {"nulls": n, "distinct": d, "min": mn, "max": mx, **({"sum": s} if numeric else {})}
    return out
