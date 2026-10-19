"""Canonical type system.

Every connector reports native type strings (``VARCHAR2(50)``, ``NUMBER(10,2)``,
``TIMESTAMP_NTZ``, ``STRUCT<a:INT>``...). They are parsed into a small set of
canonical types so that any source can be compared with any target.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Family(str, Enum):
    BOOLEAN = "boolean"
    INTEGER = "integer"
    DECIMAL = "decimal"
    FLOAT = "float"
    STRING = "string"
    BINARY = "binary"
    DATE = "date"
    TIMESTAMP = "timestamp"
    TIME = "time"
    ARRAY = "array"
    STRUCT = "struct"
    MAP = "map"
    JSON = "json"
    UNKNOWN = "unknown"


NUMERIC_FAMILIES = {Family.INTEGER, Family.DECIMAL, Family.FLOAT}
COMPLEX_FAMILIES = {Family.ARRAY, Family.STRUCT, Family.MAP, Family.JSON}


class Compatibility(str, Enum):
    SAFE = "safe"
    LOSSY = "lossy"
    INCOMPATIBLE = "incompatible"


@dataclass
class CanonicalType:
    family: Family
    bits: int | None = None  # integer / float width
    precision: int | None = None  # decimal precision
    scale: int | None = None  # decimal scale
    length: int | None = None  # string / binary max length (None = unbounded)
    tz: bool = False  # timestamp with time zone
    native: str = ""
    children: list[CanonicalType] = field(default_factory=list)

    def describe(self) -> str:
        f = self.family
        if f == Family.INTEGER:
            return f"integer({self.bits})"
        if f == Family.DECIMAL:
            return f"decimal({self.precision},{self.scale})"
        if f == Family.FLOAT:
            return f"float({self.bits})"
        if f in (Family.STRING, Family.BINARY):
            return f"{f.value}({self.length})" if self.length else f.value
        if f == Family.TIMESTAMP:
            return "timestamp_tz" if self.tz else "timestamp"
        return f.value


# Integer width by name, across dialects.
_INT_BITS = {
    "TINYINT": 8,
    "BYTEINT": 8,
    "INT1": 8,
    "SMALLINT": 16,
    "INT2": 16,
    "SHORT": 16,
    "INT": 32,
    "INTEGER": 32,
    "INT4": 32,
    "MEDIUMINT": 32,
    "SERIAL": 32,
    "BIGINT": 64,
    "INT8": 64,
    "LONG": 64,
    "BIGSERIAL": 64,
    "HUGEINT": 128,
    "UBIGINT": 64,
    "UINTEGER": 32,
    "USMALLINT": 16,
    "UTINYINT": 8,
}
_FLOAT_BITS = {
    "REAL": 32,
    "FLOAT4": 32,
    "BINARY_FLOAT": 32,
    "SMALLFLOAT": 32,
    "DOUBLE": 64,
    "DOUBLE PRECISION": 64,
    "FLOAT8": 64,
    "BINARY_DOUBLE": 64,
    "FLOAT64": 64,
}
_DECIMAL_NAMES = {"DECIMAL", "NUMERIC", "NUMBER", "DEC", "BIGNUMERIC", "BIGDECIMAL", "MONEY"}
_STRING_NAMES = {
    "VARCHAR",
    "VARCHAR2",
    "NVARCHAR",
    "NVARCHAR2",
    "CHAR",
    "NCHAR",
    "CHARACTER",
    "CHARACTER VARYING",
    "BPCHAR",
    "TEXT",
    "STRING",
    "CLOB",
    "NCLOB",
    "LONG VARCHAR",
    "NTEXT",
    "TINYTEXT",
    "MEDIUMTEXT",
    "LONGTEXT",
    "UUID",
    "UNIQUEIDENTIFIER",
    "ROWID",
    "UROWID",
    "ENUM",
    "SET",
    "CITEXT",
    "XML",
    "XMLTYPE",
    "NAME",
    "INTERVAL",
}
_BINARY_NAMES = {
    "BINARY",
    "VARBINARY",
    "BLOB",
    "RAW",
    "LONG RAW",
    "BYTEA",
    "BYTES",
    "IMAGE",
    "TINYBLOB",
    "MEDIUMBLOB",
    "LONGBLOB",
    "GEOGRAPHY",
    "GEOMETRY",
}
_JSON_NAMES = {"JSON", "JSONB", "VARIANT", "SUPER", "OBJECT"}

_PARAMS = re.compile(r"\(([^()]*)\)")


def _split_top_level(s: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in "<([":
            depth += 1
        elif ch in ">)]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        parts.append("".join(cur).strip())
    return parts


def _ints(params: str) -> list[int | None]:
    out: list[int | None] = []
    for p in params.split(","):
        p = p.strip().split()[0] if p.strip() else ""
        if p.lstrip("-").isdigit():
            out.append(int(p))
        elif p in ("*", "MAX"):
            out.append(None)
    return out


def parse_type(native: str, dialect: str = "generic") -> CanonicalType:
    """Parse a native type string from any supported dialect."""
    raw = (native or "").strip()
    t = re.sub(r"\s+", " ", raw.upper())
    dialect = (dialect or "generic").lower()

    # Complex types: Spark/Hive/Databricks STRUCT<...>, ARRAY<...>, MAP<...>,
    # DuckDB STRUCT(...), INTEGER[], MAP(...)
    if t.endswith("[]"):
        return CanonicalType(Family.ARRAY, native=raw, children=[parse_type(t[:-2], dialect)])
    for name, fam in (
        ("ARRAY", Family.ARRAY),
        ("STRUCT", Family.STRUCT),
        ("MAP", Family.MAP),
        ("LIST", Family.ARRAY),
        ("ROW", Family.STRUCT),
    ):
        if t == name or t.startswith(name + "<") or t.startswith(name + "("):
            inner = t[len(name) + 1 : -1] if len(t) > len(name) else ""
            children = []
            if fam == Family.ARRAY and inner:
                children = [parse_type(inner, dialect)]
            return CanonicalType(fam, native=raw, children=children)

    m = _PARAMS.search(t)
    params = m.group(1) if m else ""
    base = _PARAMS.sub("", t).strip()
    # Remove modifiers that do not change the family.
    base = re.sub(r"\b(UNSIGNED|SIGNED|ZEROFILL|BYTE|CHAR SEMANTICS)\b", "", base).strip()
    base = re.sub(r"\s+", " ", base)

    # Timestamps / dates / times
    if base.startswith("TIMESTAMP") or base in (
        "DATETIME",
        "DATETIME2",
        "SMALLDATETIME",
        "DATETIMEOFFSET",
        "TIMESTAMPTZ",
    ):
        tz = (
            "WITH TIME ZONE" in base
            or "WITH LOCAL TIME ZONE" in base
            or base in ("TIMESTAMPTZ", "TIMESTAMP_TZ", "TIMESTAMP_LTZ", "DATETIMEOFFSET")
        )
        if "WITHOUT TIME ZONE" in base or base == "TIMESTAMP_NTZ":
            tz = False
        # Spark/Databricks TIMESTAMP is session-time-zone aware (LTZ semantics)
        if base == "TIMESTAMP" and dialect in ("databricks", "spark", "hive"):
            tz = True
        return CanonicalType(Family.TIMESTAMP, tz=tz, native=raw)
    if base == "DATE":
        # Oracle DATE carries a time component.
        if dialect == "oracle":
            return CanonicalType(Family.TIMESTAMP, native=raw)
        return CanonicalType(Family.DATE, native=raw)
    if base.startswith("TIME"):
        return CanonicalType(Family.TIME, native=raw)

    if base in ("BOOLEAN", "BOOL", "BIT", "LOGICAL"):
        if base == "BIT" and params and _ints(params) and (_ints(params)[0] or 1) > 1:
            return CanonicalType(Family.BINARY, native=raw)
        return CanonicalType(Family.BOOLEAN, native=raw)

    if base in _INT_BITS:
        return CanonicalType(Family.INTEGER, bits=_INT_BITS[base], native=raw)

    if base in _FLOAT_BITS:
        return CanonicalType(Family.FLOAT, bits=_FLOAT_BITS[base], native=raw)
    if base == "FLOAT":
        # Oracle FLOAT(b) is a decimal float; generic FLOAT(p<=24) is single precision.
        p = _ints(params)
        if dialect == "oracle":
            return CanonicalType(Family.FLOAT, bits=64, native=raw)
        if dialect in ("databricks", "spark", "hive", "duckdb", "files"):
            return CanonicalType(Family.FLOAT, bits=32, native=raw)
        return CanonicalType(Family.FLOAT, bits=32 if (p and p[0] and p[0] <= 24) else 64, native=raw)

    if base in _DECIMAL_NAMES:
        p = _ints(params)
        precision = p[0] if p else None
        scale = p[1] if len(p) > 1 else (0 if p else None)
        if base == "NUMBER" and not p:
            # Oracle NUMBER without precision = arbitrary decimal. Snowflake NUMBER = NUMBER(38,0)
            if dialect == "snowflake":
                precision, scale = 38, 0
            else:
                precision, scale = 38, None
        elif base in ("DECIMAL", "NUMERIC", "DEC") and not p:
            defaults = {
                "databricks": (10, 0),
                "spark": (10, 0),
                "hive": (10, 0),
                "snowflake": (38, 0),
                "redshift": (18, 0),
                "duckdb": (18, 3),
            }
            precision, scale = defaults.get(dialect, (38, None))
        if base == "MONEY":
            precision, scale = 19, 4
        return CanonicalType(Family.DECIMAL, precision=precision, scale=scale, native=raw)

    if base in _JSON_NAMES:
        return CanonicalType(Family.JSON, native=raw)

    if base in _STRING_NAMES:
        p = _ints(params)
        length = p[0] if p else None
        if base in ("CLOB", "NCLOB", "TEXT", "STRING", "LONGTEXT", "NTEXT"):
            length = None
        if dialect == "snowflake" and length and length >= 16777216:
            length = None
        return CanonicalType(Family.STRING, length=length, native=raw)

    if base in _BINARY_NAMES:
        p = _ints(params)
        return CanonicalType(Family.BINARY, length=p[0] if p else None, native=raw)

    return CanonicalType(Family.UNKNOWN, native=raw)


def _int_digits(bits: int | None) -> int:
    return {8: 3, 16: 5, 32: 10, 64: 19, 128: 39}.get(bits or 64, 19)


def compatibility(src: CanonicalType, tgt: CanonicalType) -> tuple[Compatibility, str]:
    """Can every value of ``src`` be stored in ``tgt`` without losing information?"""
    S, T = src.family, tgt.family
    ok = Compatibility.SAFE
    lossy = Compatibility.LOSSY
    bad = Compatibility.INCOMPATIBLE

    if Family.UNKNOWN in (S, T):
        return lossy, f"Unrecognised type ({src.native} -> {tgt.native}); verify manually"

    if S == T:
        if S == Family.INTEGER:
            if (tgt.bits or 64) < (src.bits or 64):
                return lossy, f"Integer narrowed from {src.bits} to {tgt.bits} bits"
            return ok, "Same type family"
        if S == Family.FLOAT:
            if (tgt.bits or 64) < (src.bits or 64):
                return lossy, "Double precision narrowed to single precision"
            return ok, "Same type family"
        if S == Family.DECIMAL:
            sp, ss = src.precision, src.scale
            tp, ts = tgt.precision, tgt.scale
            if ss is None and ts is not None:
                return lossy, f"Unbounded-scale decimal stored with scale {ts}; fractional digits may be rounded"
            if ss is not None and ts is not None and ts < ss:
                return lossy, f"Scale reduced from {ss} to {ts}; values will be rounded"
            if sp is not None and tp is not None and ss is not None and ts is not None:
                if (tp - ts) < (sp - ss):
                    return lossy, f"Integer digits reduced ({sp - ss} -> {tp - ts}); large values may overflow"
            return ok, "Decimal precision/scale preserved"
        if S in (Family.STRING, Family.BINARY):
            if tgt.length is not None and (src.length is None or tgt.length < src.length):
                return (
                    lossy,
                    f"Max length reduced ({src.length or 'unbounded'} -> {tgt.length}); values may be truncated",
                )
            return ok, "Length preserved"
        if S == Family.TIMESTAMP:
            if src.tz and not tgt.tz:
                return lossy, "Time-zone aware timestamp stored without time zone; offsets are lost"
            if not src.tz and tgt.tz:
                return ok, "Timestamp gains a time zone; verify the session time zone used on load"
            return ok, "Same type family"
        if S == Family.ARRAY and src.children and tgt.children:
            level, reason = compatibility(src.children[0], tgt.children[0])
            return level, f"Array element: {reason}"
        return ok, "Same type family"

    # Cross-family conversions
    if S == Family.INTEGER and T == Family.DECIMAL:
        if tgt.precision is None or (tgt.precision - (tgt.scale or 0)) >= _int_digits(src.bits):
            return ok, "Integer fits in decimal"
        return lossy, f"Decimal({tgt.precision},{tgt.scale}) may overflow for {src.bits}-bit integers"
    if S == Family.DECIMAL and T == Family.INTEGER:
        if src.scale == 0 and src.precision is not None and src.precision <= _int_digits(tgt.bits) - 1:
            return ok, "Zero-scale decimal fits in integer"
        if src.scale == 0:
            return lossy, f"Decimal({src.precision},0) may overflow {tgt.bits}-bit integer"
        return lossy, "Fractional digits truncated when casting decimal to integer"
    if S in (Family.INTEGER, Family.DECIMAL) and T == Family.FLOAT:
        return lossy, "Exact numeric stored as floating point; precision beyond ~15 digits is lost"
    if S == Family.FLOAT and T == Family.DECIMAL:
        return lossy, "Floating point stored as decimal; values are rounded to the target scale"
    if S == Family.FLOAT and T == Family.INTEGER:
        return lossy, "Floating point truncated to integer"
    if S == Family.BOOLEAN and T in NUMERIC_FAMILIES:
        return ok, "Boolean stored as number (0/1)"
    if S in NUMERIC_FAMILIES and T == Family.BOOLEAN:
        return lossy, "Number reduced to boolean"
    if S == Family.DATE and T == Family.TIMESTAMP:
        return ok, "Date widened to timestamp"
    if S == Family.TIMESTAMP and T == Family.DATE:
        return lossy, "Time component dropped (timestamp -> date)"
    if T == Family.STRING:
        if S in COMPLEX_FAMILIES:
            return lossy, f"{S.value} serialised to string; structure must be parsed downstream"
        if tgt.length is not None and S not in (Family.BOOLEAN,):
            return lossy, f"Stored as string({tgt.length}); check the formatting and length of values"
        return ok, f"{S.value} representable as string (formatting may differ)"
    if S == Family.STRING:
        if T == Family.JSON:
            return lossy, "String must contain valid JSON"
        return lossy, f"String parsed into {T.value}; unparseable values become NULL or fail the load"
    if S in COMPLEX_FAMILIES and T in COMPLEX_FAMILIES:
        if T == Family.JSON or S == Family.JSON:
            return lossy, f"{S.value} <-> {T.value}: semi-structured conversion, verify nested fields"
        return bad, f"{S.value} cannot be stored as {T.value}"
    if S == Family.BINARY or T == Family.BINARY:
        return bad, f"{S.value} is not convertible to {T.value}"
    return bad, f"{S.value} is not convertible to {T.value}"


def comparison_family(a: CanonicalType, b: CanonicalType) -> Family:
    """Family both sides are normalised to before values are compared."""
    fa, fb = a.family, b.family
    if fa == fb:
        return fa
    if {fa, fb} <= {Family.DATE, Family.TIMESTAMP}:
        return Family.TIMESTAMP
    if fa in NUMERIC_FAMILIES and fb in NUMERIC_FAMILIES:
        if Family.FLOAT in (fa, fb) or Family.DECIMAL in (fa, fb):
            return Family.DECIMAL
        return Family.INTEGER
    if (fa in NUMERIC_FAMILIES and fb == Family.STRING) or (fb in NUMERIC_FAMILIES and fa == Family.STRING):
        return Family.DECIMAL
    if Family.BOOLEAN in (fa, fb) and (fa in NUMERIC_FAMILIES or fb in NUMERIC_FAMILIES):
        return Family.INTEGER
    return Family.STRING
