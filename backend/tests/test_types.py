import pytest

from app.engine.types import Compatibility, Family, comparison_family, compatibility, parse_type


@pytest.mark.parametrize(
    "native,dialect,family,extra",
    [
        ("NUMBER(10,2)", "oracle", Family.DECIMAL, {"precision": 10, "scale": 2}),
        ("NUMBER", "oracle", Family.DECIMAL, {"precision": 38, "scale": None}),
        ("NUMBER", "snowflake", Family.DECIMAL, {"precision": 38, "scale": 0}),
        ("VARCHAR2(50 BYTE)", "oracle", Family.STRING, {"length": 50}),
        ("DATE", "oracle", Family.TIMESTAMP, {}),
        ("DATE", "databricks", Family.DATE, {}),
        ("TIMESTAMP(6) WITH TIME ZONE", "oracle", Family.TIMESTAMP, {"tz": True}),
        ("TIMESTAMP_NTZ", "snowflake", Family.TIMESTAMP, {"tz": False}),
        ("TIMESTAMP", "databricks", Family.TIMESTAMP, {"tz": True}),
        ("TIMESTAMP WITHOUT TIME ZONE", "redshift", Family.TIMESTAMP, {"tz": False}),
        ("VARCHAR(16777216)", "snowflake", Family.STRING, {"length": None}),
        ("STRING", "hive", Family.STRING, {"length": None}),
        ("BIGINT", "hive", Family.INTEGER, {"bits": 64}),
        ("INT4", "redshift", Family.INTEGER, {"bits": 32}),
        ("DOUBLE PRECISION", "redshift", Family.FLOAT, {"bits": 64}),
        ("BINARY_DOUBLE", "oracle", Family.FLOAT, {"bits": 64}),
        ("ARRAY<STRING>", "databricks", Family.ARRAY, {}),
        ("STRUCT<a:INT,b:STRING>", "databricks", Family.STRUCT, {}),
        ("INTEGER[]", "files", Family.ARRAY, {}),
        ("VARIANT", "snowflake", Family.JSON, {}),
        ("SUPER", "redshift", Family.JSON, {}),
        ("CLOB", "oracle", Family.STRING, {"length": None}),
        ("RAW(16)", "oracle", Family.BINARY, {"length": 16}),
        ("BOOLEAN", "snowflake", Family.BOOLEAN, {}),
        ("GEOGRAPHY", "snowflake", Family.BINARY, {}),
    ],
)
def test_parse_type(native, dialect, family, extra):
    t = parse_type(native, dialect)
    assert t.family == family
    for k, v in extra.items():
        assert getattr(t, k) == v, (native, k)


@pytest.mark.parametrize(
    "src,sd,tgt,td,expected",
    [
        ("NUMBER(10,2)", "oracle", "DECIMAL(10,2)", "databricks", Compatibility.SAFE),
        ("NUMBER(12,2)", "oracle", "DECIMAL(10,1)", "databricks", Compatibility.LOSSY),
        ("NUMBER", "oracle", "NUMBER(38,0)", "snowflake", Compatibility.LOSSY),
        ("NUMBER(18,4)", "oracle", "DOUBLE", "databricks", Compatibility.LOSSY),
        ("INT", "hive", "BIGINT", "databricks", Compatibility.SAFE),
        ("BIGINT", "redshift", "INT", "databricks", Compatibility.LOSSY),
        ("VARCHAR2(100)", "oracle", "STRING", "databricks", Compatibility.SAFE),
        ("VARCHAR(500)", "redshift", "VARCHAR(100)", "databricks", Compatibility.LOSSY),
        ("DATE", "oracle", "TIMESTAMP_NTZ", "snowflake", Compatibility.SAFE),
        ("DATE", "oracle", "DATE", "snowflake", Compatibility.LOSSY),
        ("TIMESTAMP_TZ", "snowflake", "TIMESTAMP_NTZ", "databricks", Compatibility.LOSSY),
        ("STRUCT<a:INT>", "hive", "STRING", "databricks", Compatibility.LOSSY),
        ("STRUCT<a:INT>", "hive", "ARRAY<INT>", "databricks", Compatibility.INCOMPATIBLE),
        ("SUPER", "redshift", "STRUCT<a:INT>", "databricks", Compatibility.LOSSY),
        ("BLOB", "oracle", "INT", "databricks", Compatibility.INCOMPATIBLE),
        ("BIGINT", "redshift", "DECIMAL(38,0)", "databricks", Compatibility.SAFE),
        ("BIGINT", "redshift", "DECIMAL(10,0)", "databricks", Compatibility.LOSSY),
    ],
)
def test_compatibility(src, sd, tgt, td, expected):
    level, reason = compatibility(parse_type(src, sd), parse_type(tgt, td))
    assert level == expected, reason
    assert reason


def test_comparison_family():
    assert comparison_family(parse_type("DATE"), parse_type("TIMESTAMP")) == Family.TIMESTAMP
    assert comparison_family(parse_type("INT"), parse_type("BIGINT")) == Family.INTEGER
    assert comparison_family(parse_type("INT"), parse_type("DECIMAL(10,2)")) == Family.DECIMAL
    assert comparison_family(parse_type("VARCHAR(10)"), parse_type("DECIMAL(10,2)")) == Family.DECIMAL
    assert comparison_family(parse_type("VARCHAR(10)"), parse_type("DATE")) == Family.STRING
