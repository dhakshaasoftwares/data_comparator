import duckdb

from app.connectors.errors import explain
from app.engine.config import JobConfig, JobOptions
from app.engine.data_compare import ColumnPlan, keyed_diff
from app.engine.insights import table_insights
from app.engine.keys import key_candidates
from app.engine.recommend import recommend
from app.engine.runner import run_job
from app.engine.types import parse_type


def _patterns(src_type, tgt_type, rows):
    duck = duckdb.connect()
    duck.execute(f"CREATE TABLE src (id INTEGER, v {src_type})")
    duck.execute(f"CREATE TABLE tgt (id INTEGER, v {tgt_type})")
    duck.executemany("INSERT INTO src VALUES (?, ?)", [(i, s) for i, (s, _) in enumerate(rows)])
    duck.executemany("INSERT INTO tgt VALUES (?, ?)", [(i, t) for i, (_, t) in enumerate(rows)])
    opts = JobOptions().normalization
    key = ColumnPlan("id", "id", parse_type("INTEGER"), parse_type("INTEGER"), opts)
    val = ColumnPlan("v", "v", parse_type(src_type), parse_type(tgt_type), opts)
    col = keyed_diff(duck, [key, val], [key], 5)["columns"][0]
    return {p["pattern"]: p["count"] for p in col["patterns"]}


def test_string_patterns():
    pats = _patterns(
        "VARCHAR",
        "VARCHAR",
        [("abc", "ABC"), ("a b", "ab"), ("long text", "long"), ("x", None), (None, "y"), ("p", "q"), ("same", "same")],
    )
    assert pats == {
        "case_only": 1,
        "whitespace_only": 1,
        "truncated": 1,
        "null_in_target": 1,
        "null_in_source": 1,
        "other": 1,
    }


def test_numeric_patterns():
    pats = _patterns(
        "DECIMAL(12,2)", "DECIMAL(10,1)", [(1.25, 1.2), (1.35, 1.4), (5.0, -5.0), (10.0, 12.0), (3.3, 3.3)]
    )
    assert pats == {"rounding": 2, "sign_flip": 1, "other": 1}


def test_timestamp_patterns():
    pats = _patterns(
        "TIMESTAMP",
        "TIMESTAMP",
        [
            ("2024-01-01 10:00:00", "2024-01-01 15:30:00"),  # +5:30 shift
            ("2024-01-01 10:00:00.123", "2024-01-01 10:00:00"),  # sub-second
            ("2024-01-01 10:20:00", "2024-01-01 00:00:00"),  # time lost
        ],
    )
    assert pats == {"timezone_shift": 1, "sub_second": 1, "time_lost": 1}


def test_key_candidates_rank_identifiers_first():
    types = {c: parse_type("VARCHAR(10)") for c in ["name", "order_no", "id", "region_code", "notes"]}
    assert key_candidates(list(types), types) == ["id", "order_no", "region_code", "name"]


def test_insights_with_fixes(demo_connectors):
    src, tgt = demo_connectors
    cfg = JobConfig(
        source_connection_id=1,
        target_connection_id=2,
        tables=[
            {"source_table": "CUSTOMERS", "target_table": "customers"},
            {"source_table": "ORDERS", "target_table": "orders"},
        ],
    )
    res = run_job(cfg, src, tgt)
    titles = [i["title"] for i in res["insights"]]
    assert "3 row(s) missing in the target" in titles
    assert "AMOUNT: numbers are rounded in the target" in titles
    assert "AMOUNT: signs are reversed" in titles
    case = next(i for i in res["insights"] if i["title"].startswith("EMAIL"))
    assert case["fix"]["path"] == ["normalization", "case_insensitive"]
    rounding = next(i for i in res["insights"] if "rounded" in i["title"])
    assert rounding["fix"]["value"] == 0.05

    # applying the suggested fixes removes those findings
    opts = cfg.options.model_copy(deep=True)
    opts.normalization.case_insensitive = True
    opts.normalization.numeric_tolerance = 0.05
    res2 = run_job(cfg.model_copy(update={"options": opts}), src, tgt)
    titles2 = [i["title"] for i in res2["insights"]]
    assert not any(t.startswith("EMAIL") or "rounded" in t for t in titles2)
    assert "AMOUNT: signs are reversed" in titles2


def test_key_is_detected_without_primary_key(demo_connectors):
    _, lake = demo_connectors
    cfg = JobConfig(
        source_connection_id=1, target_connection_id=2, tables=[{"source_table": "orders", "target_table": "orders"}]
    )
    t = run_job(cfg, lake, lake)["tables"][0]
    assert t["key_columns"] == ["order_id"] and t["key_source"] == "detected"
    assert t["row_diff"]["method"] == "key" and t["score"] == 100


def test_missing_columns_insight_offers_exclude():
    t = {
        "source_table": "A",
        "target_table": "a",
        "schema": {"columns": [], "missing_in_target": [{"column": "OLD", "type": "INT"}]},
        "counts": {"source": 1, "target": 1},
    }
    (ins,) = table_insights(t, JobOptions())
    assert ins["fix"] == {"kind": "table", "label": "Exclude these columns", "patch": {"exclude_columns": ["OLD"]}}


def test_recommendations():
    rec = recommend("oracle", "snowflake")
    assert rec["normalization"]["null_equals_empty"] is True
    assert "Oracle → Snowflake" in rec["title"]
    assert recommend("sqlserver", "databricks")["normalization"]["timestamp_precision"] == "millisecond"


def test_error_hints():
    assert "service name" in explain("DPY-6001: cannot connect to database (CONNECTION_ID=...) service name X")
    assert "Sign-in was rejected" in explain("ORA-01017: invalid username/password; logon denied")
    assert "could not be reached" in explain("connection timed out")
    assert "account identifier" in explain("250001 (08001): Failed to connect")
    assert explain("something unusual") is None
