"""Recommended comparison settings for a source -> target platform pair."""

from __future__ import annotations

PLATFORM_NAMES = {
    "oracle": "Oracle",
    "snowflake": "Snowflake",
    "databricks": "Databricks",
    "hive": "Hive",
    "redshift": "Redshift",
    "files": "Parquet/files",
    "bigquery": "BigQuery",
    "teradata": "Teradata",
    "sqlserver": "SQL Server",
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "sqlite": "SQLite",
    "generic": "this engine",
}


def recommend(source_dialect: str, target_dialect: str) -> dict:
    """Normalisation settings plus the reasons, in plain language."""
    norm: dict = {"trim_strings": True, "null_equals_empty": False, "timestamp_precision": "microsecond"}
    reasons: list[str] = []
    s, t = source_dialect, target_dialect
    pair = {s, t}

    if "oracle" in pair:
        norm["null_equals_empty"] = True
        reasons.append("Oracle stores empty text as NULL, so '' and NULL are treated as equal.")
    if pair & {"oracle", "teradata", "sqlserver", "postgres", "redshift", "sqlite", "mysql"}:
        reasons.append("CHAR columns are padded with spaces, so leading/trailing spaces are ignored.")
    else:
        reasons.append("Leading/trailing spaces are ignored (switch off if spaces are meaningful in your data).")
    if "sqlserver" in pair:
        norm["timestamp_precision"] = "millisecond"
        reasons.append("SQL Server DATETIME is only accurate to ~3 ms, so timestamps are compared to the millisecond.")
    if pair & {"databricks", "hive", "files"} and pair & {"oracle", "snowflake", "redshift", "teradata", "sqlserver"}:
        reasons.append(
            "Spark/Databricks TIMESTAMP depends on the session time zone; if times look shifted by whole hours, "
            "check spark.sql.session.timeZone on the load job."
        )
    if s == "oracle":
        reasons.append("Oracle DATE carries a time of day; the target should use a TIMESTAMP to keep it.")
    if "snowflake" in pair:
        reasons.append("Snowflake folds unquoted names to upper case; columns are matched regardless of case.")
    if pair & {"hive", "files", "databricks", "snowflake"}:
        reasons.append(
            "Tables without a declared primary key get a unique key column detected automatically for row-level "
            "matching."
        )

    names = f"{PLATFORM_NAMES.get(s, s)} → {PLATFORM_NAMES.get(t, t)}"
    return {"title": f"Recommended settings for {names}", "normalization": norm, "reasons": reasons}
