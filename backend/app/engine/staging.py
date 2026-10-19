"""Helpers for the local DuckDB engine used to compare staged data."""

from __future__ import annotations

from .types import CanonicalType, Family


def qi(name: str) -> str:
    """Quote a DuckDB identifier."""
    return '"' + name.replace('"', '""') + '"'


def ql(value: str) -> str:
    """Quote a DuckDB string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def duck_type(ct: CanonicalType) -> str:
    """DuckDB storage type for a canonical type when staging rows from a database."""
    f = ct.family
    if f == Family.BOOLEAN:
        return "BOOLEAN"
    if f == Family.INTEGER:
        return "HUGEINT" if (ct.bits or 64) > 64 else "BIGINT"
    if f == Family.DECIMAL:
        if ct.precision and ct.precision <= 38 and ct.scale is not None and 0 <= ct.scale <= ct.precision:
            return f"DECIMAL({ct.precision},{ct.scale})"
        return "VARCHAR"  # keep arbitrary-precision numbers exact as text
    if f == Family.FLOAT:
        return "DOUBLE"
    if f == Family.BINARY:
        return "BLOB"
    if f == Family.DATE:
        return "DATE"
    if f == Family.TIMESTAMP:
        return "TIMESTAMPTZ" if ct.tz else "TIMESTAMP"
    if f == Family.TIME:
        return "TIME"
    return "VARCHAR"
