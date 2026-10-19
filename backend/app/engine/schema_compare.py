"""Schema compatibility between a source and a target table."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from ..connectors.base import ColumnInfo
from .config import ColumnMapping
from .types import CanonicalType, Compatibility, compatibility, parse_type

_COMPAT_POINTS = {Compatibility.SAFE: 1.0, Compatibility.LOSSY: 0.5, Compatibility.INCOMPATIBLE: 0.0}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass
class ColumnPair:
    source: str
    target: str
    source_type: str
    target_type: str
    source_canonical: str
    target_canonical: str
    compatibility: str
    reason: str
    match: str  # mapping | exact | case_insensitive | normalized
    warnings: list[str] = field(default_factory=list)


@dataclass
class SchemaResult:
    columns: list[ColumnPair]
    missing_in_target: list[dict]
    extra_in_target: list[dict]
    score: float  # 0..1
    summary: dict

    # canonical types kept for the data comparison step (not serialised)
    source_types: dict[str, CanonicalType] = field(default_factory=dict, repr=False)
    target_types: dict[str, CanonicalType] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        return {
            "columns": [asdict(c) for c in self.columns],
            "missing_in_target": self.missing_in_target,
            "extra_in_target": self.extra_in_target,
            "score": round(self.score, 4),
            "summary": self.summary,
        }


def match_columns(
    source: list[str], target: list[str], mappings: list[ColumnMapping] | None = None
) -> list[tuple[str, str, str]]:
    """Pair source and target column names. Returns (source, target, method)."""
    pairs: list[tuple[str, str, str]] = []
    used_t: set[str] = set()
    used_s: set[str] = set()
    tset = set(target)
    for m in mappings or []:
        if m.source in source and m.target in tset:
            pairs.append((m.source, m.target, "mapping"))
            used_s.add(m.source)
            used_t.add(m.target)

    def pass_(key, method):
        index: dict[str, str] = {}
        for t in target:
            if t not in used_t:
                index.setdefault(key(t), t)
        for s in source:
            if s in used_s:
                continue
            t = index.get(key(s))
            if t and t not in used_t:
                pairs.append((s, t, method))
                used_s.add(s)
                used_t.add(t)

    pass_(lambda n: n, "exact")
    pass_(lambda n: n.lower(), "case_insensitive")
    pass_(_norm, "normalized")
    order = {s: i for i, s in enumerate(source)}
    return sorted(pairs, key=lambda p: order[p[0]])


def compare_schema(
    src_cols: list[ColumnInfo],
    tgt_cols: list[ColumnInfo],
    src_dialect: str,
    tgt_dialect: str,
    mappings: list[ColumnMapping] | None = None,
    exclude: list[str] | None = None,
) -> SchemaResult:
    exclude_set = {e.lower() for e in exclude or []}
    src = [c for c in src_cols if c.name.lower() not in exclude_set]
    s_by = {c.name: c for c in src}
    t_by = {c.name: c for c in tgt_cols}
    s_types = {c.name: parse_type(c.native_type, src_dialect) for c in src}
    t_types = {c.name: parse_type(c.native_type, tgt_dialect) for c in tgt_cols}

    pairs = match_columns([c.name for c in src], [c.name for c in tgt_cols], mappings)
    columns: list[ColumnPair] = []
    points = 0.0
    counts = {"safe": 0, "lossy": 0, "incompatible": 0}
    for s, t, method in pairs:
        level, reason = compatibility(s_types[s], t_types[t])
        warnings = []
        if s_by[s].nullable and not t_by[t].nullable:
            warnings.append("Target column is NOT NULL but source allows NULLs")
        if method == "normalized":
            warnings.append(f"Column renamed: {s} -> {t}")
        columns.append(
            ColumnPair(
                source=s,
                target=t,
                source_type=s_by[s].native_type,
                target_type=t_by[t].native_type,
                source_canonical=s_types[s].describe(),
                target_canonical=t_types[t].describe(),
                compatibility=level.value,
                reason=reason,
                match=method,
                warnings=warnings,
            )
        )
        points += _COMPAT_POINTS[level]
        counts[level.value] += 1

    matched_s = {p[0] for p in pairs}
    matched_t = {p[1] for p in pairs}
    missing = [{"column": c.name, "type": c.native_type} for c in src if c.name not in matched_s]
    extra = [{"column": c.name, "type": c.native_type} for c in tgt_cols if c.name not in matched_t]
    score = points / len(src) if src else 1.0
    summary = {
        "source_columns": len(src),
        "target_columns": len(tgt_cols),
        "matched": len(pairs),
        "missing_in_target": len(missing),
        "extra_in_target": len(extra),
        **counts,
    }
    return SchemaResult(columns, missing, extra, score, summary, s_types, t_types)
