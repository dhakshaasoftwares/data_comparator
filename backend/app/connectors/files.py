"""File / data-lake connector (Parquet, ORC-converted Parquet, CSV, JSON, Delta, Iceberg).

Reads Spark/Hive style table folders (``/warehouse/orders/dt=2024-01-01/part-0.parquet``)
from local disk, S3, ADLS/Azure Blob or GCS through DuckDB.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb

from ..engine.staging import qi, ql
from ..settings import get_settings
from .base import ColumnInfo, ConfigField, Connector, ConnectorError, ConnectorSpec

CLOUD_PREFIXES = ("s3://", "s3a://", "gs://", "gcs://", "az://", "abfss://", "azure://", "http://", "https://")
FORMATS = ["parquet", "csv", "json", "delta", "iceberg"]


class FilesConnector(Connector):
    spec = ConnectorSpec(
        type="files",
        label="Files / Data lake (Parquet, CSV, Delta, Iceberg)",
        category="Files",
        dialect="files",
        description="Spark/Hive table folders or files on local disk, S3, ADLS or GCS. "
        "Each sub-folder (or file) under the base path is a table.",
        fields=[
            ConfigField(
                "base_path",
                "Base path",
                required=True,
                placeholder="s3://lake/warehouse or demo/spark_parquet",
                help="Local paths are relative to the server data folder",
            ),
            ConfigField("format", "Format", "select", default="parquet", options=FORMATS),
            ConfigField("hive_partitioning", "Hive-style partition folders (col=value)", "boolean", default=True),
            ConfigField("csv_delimiter", "CSV delimiter", default=",", advanced=True),
            ConfigField("s3_region", "S3 region", advanced=True),
            ConfigField("s3_access_key_id", "S3 access key ID", advanced=True),
            ConfigField("s3_secret_access_key", "S3 secret access key", "password", secret=True, advanced=True),
            ConfigField("s3_endpoint", "S3 endpoint (MinIO / custom)", advanced=True),
            ConfigField(
                "azure_connection_string", "Azure storage connection string", "password", secret=True, advanced=True
            ),
            ConfigField("gcs_hmac_key_id", "GCS HMAC key ID", advanced=True),
            ConfigField("gcs_hmac_secret", "GCS HMAC secret", "password", secret=True, advanced=True),
        ],
    )

    def __init__(self, config):
        super().__init__(config)
        self._duck: duckdb.DuckDBPyConnection | None = None

    # -- paths -------------------------------------------------------------
    @property
    def is_cloud(self) -> bool:
        return str(self.config.get("base_path", "")).startswith(CLOUD_PREFIXES)

    @property
    def base(self) -> str:
        raw = str(self.config.get("base_path", "")).strip()
        if not raw:
            raise ConnectorError("Base path is required")
        if self.is_cloud:
            return raw.rstrip("/")
        root = get_settings().data_root
        path = (root / raw).resolve()
        if path != root and root not in path.parents:
            raise ConnectorError("Local paths must be inside the server data folder")
        return str(path)

    @property
    def fmt(self) -> str:
        return self.config.get("format") or "parquet"

    def allowed_paths(self) -> list[str]:
        return [self.base + "/"]

    def _table_path(self, table: str) -> str:
        if ".." in table.split("/"):
            raise ConnectorError("Invalid table path")
        return f"{self.base}/{table.strip('/')}"

    def _source(self, table: str) -> str:
        """DuckDB table function reading a table."""
        path = self._table_path(table)
        fmt = self.fmt
        if fmt == "delta":
            return f"delta_scan({ql(path)})"
        if fmt == "iceberg":
            return f"iceberg_scan({ql(path)}, allow_moved_paths = true)"
        is_file = (
            Path(path).suffix.lower() in (".parquet", ".csv", ".json", ".jsonl", ".gz")
            if not self.is_cloud
            else path.rsplit("/", 1)[-1].count(".") > 0
        )
        hive = "true" if self.config.get("hive_partitioning", True) not in (False, "false") else "false"
        ext = {"parquet": "parquet", "csv": "csv", "json": "json"}[fmt]
        target = path if is_file else f"{path}/**/*.{ext}*"
        if fmt == "parquet":
            return f"read_parquet({ql(target)}, hive_partitioning = {hive}, union_by_name = true)"
        if fmt == "csv":
            delim = self.config.get("csv_delimiter") or ","
            return (
                f"read_csv({ql(target)}, hive_partitioning = {hive}, union_by_name = true, "
                f"delim = {ql(delim)}, header = true)"
            )
        return f"read_json_auto({ql(target)}, hive_partitioning = {hive}, union_by_name = true)"

    # -- DuckDB session ----------------------------------------------------
    def prepare_duckdb(self, duck: duckdb.DuckDBPyConnection) -> None:
        c = self.config
        if self.fmt in ("delta", "iceberg"):
            duck.execute(f"INSTALL {self.fmt}; LOAD {self.fmt};")
        if not self.is_cloud:
            return
        duck.execute("INSTALL httpfs; LOAD httpfs;")
        base = self.base
        if base.startswith(("s3://", "s3a://")) and c.get("s3_access_key_id"):
            parts = [
                "TYPE s3",
                f"KEY_ID {ql(c['s3_access_key_id'])}",
                f"SECRET {ql(c.get('s3_secret_access_key', ''))}",
            ]
            if c.get("s3_region"):
                parts.append(f"REGION {ql(c['s3_region'])}")
            if c.get("s3_endpoint"):
                parts += [f"ENDPOINT {ql(c['s3_endpoint'])}", "URL_STYLE 'path'"]
            duck.execute(f"CREATE OR REPLACE SECRET ({', '.join(parts)})")
        elif base.startswith(("az://", "abfss://", "azure://")):
            duck.execute("INSTALL azure; LOAD azure;")
            if c.get("azure_connection_string"):
                duck.execute(
                    f"CREATE OR REPLACE SECRET (TYPE azure, CONNECTION_STRING {ql(c['azure_connection_string'])})"
                )
        elif base.startswith(("gs://", "gcs://")) and c.get("gcs_hmac_key_id"):
            duck.execute(
                f"CREATE OR REPLACE SECRET (TYPE gcs, KEY_ID {ql(c['gcs_hmac_key_id'])}, "
                f"SECRET {ql(c.get('gcs_hmac_secret', ''))})"
            )

    @property
    def duck(self) -> duckdb.DuckDBPyConnection:
        if self._duck is None:
            self._duck = sandboxed_duckdb([self], ":memory:")
        return self._duck

    def _run(self, sql: str, params=None):
        try:
            return self.duck.execute(sql, params or [])
        except duckdb.Error as exc:
            raise ConnectorError(str(exc).splitlines()[0]) from exc

    # -- contract ----------------------------------------------------------
    def test(self) -> None:
        if not self.is_cloud and not os.path.isdir(self.base):
            raise ConnectorError(f"Folder not found: {self.config.get('base_path')}")
        self.list_tables()

    def list_tables(self) -> list[str]:
        base = self.base
        if not self.is_cloud:
            if not os.path.isdir(base):
                raise ConnectorError(f"Folder not found: {self.config.get('base_path')}")
            names = []
            for entry in sorted(os.listdir(base)):
                if entry.startswith((".", "_")):
                    continue
                full = os.path.join(base, entry)
                if os.path.isdir(full) or entry.lower().endswith((".parquet", ".csv", ".json", ".jsonl")):
                    names.append(entry)
            return names
        rows = self._run(f"SELECT file FROM glob({ql(base + '/**')})").fetchall()
        names = set()
        for (f,) in rows:
            rel = f[len(base) + 1 :]
            names.add(rel.split("/", 1)[0])
        return sorted(n for n in names if n and not n.startswith((".", "_")))

    def get_columns(self, table: str) -> list[ColumnInfo]:
        rows = self._run(f"DESCRIBE SELECT * FROM {self._source(table)}").fetchall()
        return [ColumnInfo(r[0], r[1], (r[2] or "YES") == "YES") for r in rows]

    def count_rows(self, table: str, where: str | None = None) -> int:
        sql = f"SELECT count(*) FROM {self._source(table)}" + (f" WHERE {where}" if where else "")
        return int(self._run(sql).fetchone()[0])

    def profile(self, table, columns, where=None):
        parts = []
        for i, (name, numeric) in enumerate(columns):
            c = qi(name)
            parts += [
                f"count(*) - count({c}) AS n{i}",
                f"count(DISTINCT {c}) AS d{i}",
                f"min({c}) AS mn{i}",
                f"max({c}) AS mx{i}",
            ]
            if numeric:
                parts.append(f"sum({c}) AS s{i}")
        sql = f"SELECT {', '.join(parts)} FROM {self._source(table)}" + (f" WHERE {where}" if where else "")
        cur = self._run(sql)
        row = dict(zip([d[0] for d in cur.description], cur.fetchone()))
        out = {}
        for i, (name, numeric) in enumerate(columns):
            stats = {"nulls": row[f"n{i}"], "distinct": row[f"d{i}"], "min": row[f"mn{i}"], "max": row[f"mx{i}"]}
            if numeric:
                stats["sum"] = row[f"s{i}"]
            out[name] = stats
        return out

    def stage(self, duck, name, table, columns, where=None, limit=None, order_by=None, upper_bound=None) -> int:
        cols = ", ".join(qi(c) for c in columns)
        sql = f"CREATE OR REPLACE TABLE {qi(name)} AS SELECT {cols} FROM {self._source(table)}"
        conds, params = [], []
        if where:
            conds.append(f"({where})")
        if upper_bound is not None:
            col, value, inclusive = upper_bound
            conds.append(f"{qi(col)} {'<=' if inclusive else '<'} ?")
            params.append(value)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        if order_by:
            sql += " ORDER BY " + ", ".join(qi(c) for c in order_by)
        if limit:
            sql += f" LIMIT {int(limit)}"
        duck.execute(sql, params)
        return int(duck.execute(f"SELECT count(*) FROM {qi(name)}").fetchone()[0])

    def close(self) -> None:
        if self._duck is not None:
            self._duck.close()
            self._duck = None


def sandboxed_duckdb(connectors: list[Connector], database: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """A DuckDB session that can only read the paths the given connectors need.

    User supplied SQL (filters, quality expressions) runs inside this session,
    so external file access is locked down before any of it executes.
    """
    duck = duckdb.connect(database)
    settings = get_settings()
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    duck.execute(f"SET temp_directory = {ql(str(settings.work_dir / 'duck_tmp'))}")
    duck.execute("SET TimeZone = 'UTC'")
    allowed: list[str] = []
    for c in connectors:
        c.prepare_duckdb(duck)
        allowed += c.allowed_paths()
    if allowed:
        duck.execute(f"SET allowed_directories = [{', '.join(ql(p) for p in allowed)}]")
    duck.execute("SET enable_external_access = false")
    duck.execute("SET lock_configuration = true")
    return duck
