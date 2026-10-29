"""Find a unique key for tables without a declared primary key (Hive, Parquet, Snowflake, ...)."""

from __future__ import annotations

import re

from ..connectors import Connector
from .types import CanonicalType, Family

_KEY_FAMILIES = {Family.INTEGER, Family.DECIMAL, Family.STRING, Family.DATE, Family.TIMESTAMP}
_STRONG = re.compile(r"^(id|key|pk|uuid|guid)$", re.I)
_HINT = re.compile(r"(_id|id|_key|_pk|_no|_num|_nbr|_number|_code|_uuid)$", re.I)
MAX_CANDIDATES = 6


def key_candidates(columns: list[str], types: dict[str, CanonicalType]) -> list[str]:
    """Columns that look like identifiers, best guesses first."""
    ranked = []
    for pos, name in enumerate(columns):
        if types[name].family not in _KEY_FAMILIES:
            continue
        if _STRONG.match(name):
            rank = 0
        elif _HINT.search(name):
            rank = 1
        elif pos == 0:
            rank = 2
        else:
            continue
        ranked.append((rank, pos, name))
    return [name for _, _, name in sorted(ranked)[:MAX_CANDIDATES]]


def detect_unique_key(
    conn: Connector,
    table: str,
    columns: list[str],
    types: dict[str, CanonicalType],
    where: str | None = None,
    row_count: int | None = None,
) -> list[str]:
    """Return ``[column]`` for the first candidate that is unique and never NULL, else ``[]``.

    Runs one aggregate query inside the engine; no data is moved.
    """
    candidates = key_candidates(columns, types)
    if not candidates:
        return []
    if row_count is None:
        row_count = conn.count_rows(table, where)
    if not row_count:
        return []
    stats = conn.profile(table, [(c, False) for c in candidates], where)
    for c in candidates:
        s = stats.get(c, {})
        if "error" not in s and s.get("nulls") == 0 and s.get("distinct") == row_count:
            return [c]
    return []
