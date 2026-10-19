import duckdb
import pytest

from app.connectors.base import ColumnInfo, ConnectorError
from app.connectors.files import FilesConnector
from app.engine.config import ColumnMapping, JobConfig, JobOptions, Normalization, QualityRule
from app.engine.data_compare import ColumnPlan, keyed_diff, keyless_diff
from app.engine.quality import run_rule
from app.engine.runner import run_job
from app.engine.schema_compare import compare_schema, match_columns
from app.engine.types import parse_type


def test_match_columns_methods():
    pairs = match_columns(
        ["CUSTOMER_ID", "Email", "first_name", "zip"],
        ["customer_id", "email", "FirstName", "x"],
        [ColumnMapping(source="zip", target="x")],
    )
    assert ("zip", "x", "mapping") in pairs
    assert ("CUSTOMER_ID", "customer_id", "case_insensitive") in pairs
    assert ("first_name", "FirstName", "normalized") in pairs


def test_compare_schema_missing_and_extra():
    src = [ColumnInfo("ID", "NUMBER(10,0)", False), ColumnInfo("NAME", "VARCHAR2(50)"), ColumnInfo("GONE", "DATE")]
    tgt = [ColumnInfo("id", "BIGINT"), ColumnInfo("name", "STRING"), ColumnInfo("load_ts", "TIMESTAMP")]
    res = compare_schema(src, tgt, "oracle", "databricks")
    assert [m["column"] for m in res.missing_in_target] == ["GONE"]
    assert [e["column"] for e in res.extra_in_target] == ["load_ts"]
    assert res.summary["matched"] == 2
    assert 0 < res.score < 1


def _plans(norm=None):
    norm = norm or Normalization()
    s = {"id": parse_type("INTEGER"), "name": parse_type("VARCHAR(20)"), "amt": parse_type("DECIMAL(10,2)")}
    t = {"id": parse_type("BIGINT"), "name": parse_type("STRING"), "amt": parse_type("DOUBLE")}
    return [ColumnPlan(c, c, s[c], t[c], norm) for c in ("id", "name", "amt")]


def _duck(src_rows, tgt_rows):
    duck = duckdb.connect()
    duck.execute("CREATE TABLE src (id INTEGER, name VARCHAR, amt DECIMAL(10,2))")
    duck.execute("CREATE TABLE tgt (id BIGINT, name VARCHAR, amt DOUBLE)")
    for table, rows in (("src", src_rows), ("tgt", tgt_rows)):
        if rows:
            duck.executemany(f"INSERT INTO {table} VALUES (?,?,?)", rows)
    return duck


def test_keyed_diff_counts_and_samples():
    duck = _duck(
        [(1, "a ", 1.10), (2, "b", 2.00), (3, "c", 3.00), (4, "", 4.0)],
        [(1, "a", 1.1), (2, "B", 2.0), (5, "e", 5.0), (4, None, 4.0)],
    )
    plans = _plans()
    res = keyed_diff(duck, plans, [plans[0]], sample_size=10)
    assert res["matched_rows"] == 3
    assert res["only_in_source"] == 1 and res["only_in_target"] == 1
    assert res["mismatched_rows"] == 1  # 'b' vs 'B'; trailing space and ''/NULL are normalised
    assert res["samples"]["mismatches"][0]["key"] == {"id": 2}
    assert res["samples"]["only_in_source"] == [{"id": 3}]


def test_keyed_diff_case_insensitive_and_tolerance():
    norm = Normalization(case_insensitive=True, numeric_tolerance=0.05)
    duck = _duck([(1, "A", 1.00), (2, "b", 2.00)], [(1, "a", 1.04), (2, "B", 2.10)])
    plans = _plans(norm)
    res = keyed_diff(duck, plans, [plans[0]], sample_size=10)
    assert res["mismatched_rows"] == 1
    assert {c["source"]: c["mismatches"] for c in res["columns"]} == {"name": 0, "amt": 1}


def test_keyless_diff_multiset():
    duck = _duck([(1, "a", 1), (1, "a", 1), (2, "b", 2)], [(1, "a", 1), (2, "b", 2), (3, "c", 3)])
    res = keyless_diff(duck, _plans(), 10)
    assert (res["matched_rows"], res["only_in_source"], res["only_in_target"]) == (2, 1, 1)


def test_quality_rules():
    duck = _duck([], [(1, "x@y.com", -1), (2, None, 5), (2, "bad", 7)])
    cols = {"id", "name", "amt"}
    r = run_rule(duck, "tgt", "target", QualityRule(type="range", column="amt", min=0), cols, {})
    assert r["violations"] == 1 and not r["passed"]
    r = run_rule(duck, "tgt", "target", QualityRule(type="unique", column="id"), cols, {})
    assert r["violations"] == 2
    r = run_rule(duck, "tgt", "target", QualityRule(type="regex", column="name", pattern=r"[^@]+@[^@]+"), cols, {})
    assert r["violations"] == 1
    r = run_rule(duck, "tgt", "target", QualityRule(type="not_null", column="NAME", max_violation_pct=50), cols, {})
    assert r["violations"] == 1 and r["passed"]
    r = run_rule(duck, "tgt", "target", QualityRule(type="expression", expression="amt < 100"), cols, {})
    assert r["violations"] == 0 and r["passed"]
    r = run_rule(duck, "tgt", "target", QualityRule(type="not_null", column="nope"), cols, {})
    assert "error" in r and not r["passed"]


def test_end_to_end_demo(demo_connectors):
    src, tgt = demo_connectors
    cfg = JobConfig(
        source_connection_id=1,
        target_connection_id=2,
        tables=[
            {
                "source_table": "CUSTOMERS",
                "target_table": "customers",
                "rules": [{"type": "not_null", "column": "city"}],
            },
            {"source_table": "ORDERS", "target_table": "orders"},
            {"source_table": "PRODUCTS", "target_table": "products"},
            {"source_table": "MISSING", "target_table": "nothing"},
        ],
    )
    res = run_job(cfg, src, tgt)
    by = {t["source_table"]: t for t in res["tables"]}
    cust = by["CUSTOMERS"]["row_diff"]
    assert (cust["only_in_source"], cust["only_in_target"], cust["mismatched_rows"]) == (3, 2, 9)
    assert by["CUSTOMERS"]["quality"][0]["violations"] == 4
    orders = by["ORDERS"]
    assert orders["row_diff"]["columns"][3]["source"] == "AMOUNT"
    assert orders["row_diff"]["columns"][3]["mismatches"] > 0
    assert any(c["compatibility"] == "lossy" for c in orders["schema"]["columns"])
    assert by["PRODUCTS"]["score"] == 100 and by["PRODUCTS"]["status"] == "pass"
    assert by["MISSING"]["status"] == "error"
    assert res["summary"]["errors"] == 1


@pytest.mark.parametrize("mode", ["counts", "profile"])
def test_lighter_modes(demo_connectors, mode):
    src, tgt = demo_connectors
    cfg = JobConfig(
        source_connection_id=1,
        target_connection_id=2,
        tables=[{"source_table": "PRODUCTS", "target_table": "products"}],
        options=JobOptions(mode=mode),
    )
    table = run_job(cfg, src, tgt)["tables"][0]
    assert "row_diff" not in table
    assert table["score"] == 100
    assert ("profile" in table) == (mode == "profile")


def test_sampled_window_is_aligned(demo_connectors):
    src, tgt = demo_connectors
    cfg = JobConfig(
        source_connection_id=1,
        target_connection_id=2,
        tables=[{"source_table": "ORDERS", "target_table": "orders"}],
        options=JobOptions(max_rows=500, normalization=Normalization(numeric_tolerance=0.06)),
    )
    diff = run_job(cfg, src, tgt)["tables"][0]["row_diff"]
    assert diff["sampled"]
    assert diff["only_in_source"] == 0 and diff["only_in_target"] == 0
    assert diff["matched_rows"] == diff["staged_rows"]["source"]


def test_files_connector_sandbox(data_root):
    with pytest.raises(ConnectorError):
        FilesConnector({"base_path": "../../etc"}).list_tables()
    conn = FilesConnector({"base_path": "demo/spark_lake"})
    with pytest.raises(ConnectorError):
        conn.count_rows("orders", "(SELECT count(*) FROM read_csv('/etc/passwd')) > 0")
