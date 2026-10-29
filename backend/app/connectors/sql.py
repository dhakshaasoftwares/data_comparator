"""SQLAlchemy-based connectors: Oracle, Snowflake, Redshift, Databricks, Hive, ..."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pandas as pd
import sqlalchemy as sa

from ..engine.staging import duck_type, qi
from ..engine.types import parse_type
from ..settings import get_settings
from .base import ColumnInfo, ConfigField, Connector, ConnectorError, ConnectorSpec

CHUNK_ROWS = 50_000


def _host_fields(port: int, database_label: str = "Database") -> list[ConfigField]:
    return [
        ConfigField("host", "Host", required=True, placeholder="db.example.com"),
        ConfigField("port", "Port", "number", default=port),
        ConfigField("database", database_label, required=True),
        ConfigField("username", "Username", required=True),
        ConfigField("password", "Password", "password", secret=True),
    ]


def split_table(table: str) -> tuple[str | None, str]:
    if "." in table:
        schema, name = table.rsplit(".", 1)
        return schema, name
    return None, table


class SQLAlchemyConnector(Connector):
    """Base class: subclasses only describe how to build the connection URL."""

    driver_module: str | None = None  # import name of the DB-API driver

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self._engine: sa.Engine | None = None

    # -- to override -------------------------------------------------------
    def url(self) -> sa.URL | str:
        raise NotImplementedError

    def engine_kwargs(self) -> dict[str, Any]:
        return {}

    def default_schema(self) -> str | None:
        return self.config.get("schema") or None

    # -- plumbing ----------------------------------------------------------
    @property
    def engine(self) -> sa.Engine:
        if self._engine is None:
            if self.driver_module and importlib.util.find_spec(self.driver_module) is None:
                raise ConnectorError(
                    f"Driver for {self.spec.label} is not installed. Run: pip install {self.spec.driver_hint}"
                )
            try:
                self._engine = sa.create_engine(self.url(), pool_pre_ping=True, **self.engine_kwargs())
            except Exception as exc:  # unknown dialect, bad URL...
                raise ConnectorError(f"{self.spec.label}: {exc}") from exc
        return self._engine

    def _table(self, table: str, columns: list[str] | None = None) -> sa.TableClause:
        schema, name = split_table(table)
        schema = schema or self.default_schema()
        return sa.table(name, *[sa.column(c) for c in (columns or [])], schema=schema)

    def test(self) -> None:
        try:
            with self.engine.connect() as conn:
                conn.execute(sa.text(self.test_query()))
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(str(exc).splitlines()[0]) from exc

    def test_query(self) -> str:
        return "SELECT 1"

    def list_tables(self) -> list[str]:
        try:
            insp = sa.inspect(self.engine)
            schema = self.default_schema()
            names = insp.get_table_names(schema=schema)
            try:
                names += insp.get_view_names(schema=schema)
            except Exception:
                pass
        except ConnectorError:
            raise
        except Exception as exc:
            raise ConnectorError(str(exc).splitlines()[0]) from exc
        return sorted(set(names))

    def get_columns(self, table: str) -> list[ColumnInfo]:
        schema, name = split_table(table)
        try:
            cols = sa.inspect(self.engine).get_columns(name, schema=schema or self.default_schema())
        except ConnectorError:
            raise
        except sa.exc.NoSuchTableError as exc:
            raise ConnectorError(f"Table {table} not found") from exc
        except Exception as exc:
            raise ConnectorError(f"Cannot read columns of {table}: {str(exc).splitlines()[0]}") from exc
        if not cols:
            raise ConnectorError(f"Table {table} not found or has no columns")
        out = []
        for c in cols:
            try:
                native = c["type"].compile(dialect=self.engine.dialect)
            except Exception:
                native = str(c["type"])
            out.append(ColumnInfo(c["name"], native, bool(c.get("nullable", True))))
        return out

    def get_primary_key(self, table: str) -> list[str]:
        schema, name = split_table(table)
        try:
            pk = sa.inspect(self.engine).get_pk_constraint(name, schema=schema or self.default_schema())
            return list(pk.get("constrained_columns") or [])
        except Exception:
            return []

    def count_rows(self, table: str, where: str | None = None) -> int:
        stmt = sa.select(sa.func.count()).select_from(self._table(table))
        if where:
            stmt = stmt.where(sa.text(where))
        with self.engine.connect() as conn:
            return int(conn.execute(stmt).scalar() or 0)

    def profile(self, table: str, columns: list[tuple[str, bool]], where: str | None = None) -> dict[str, dict]:
        t = self._table(table, [c for c, _ in columns])

        def exprs_for(i: int, name: str, numeric: bool) -> list:
            col = t.c[name]
            e = [
                (sa.func.count() - sa.func.count(col)).label(f"n{i}"),
                sa.func.count(sa.distinct(col)).label(f"d{i}"),
                sa.func.min(col).label(f"mn{i}"),
                sa.func.max(col).label(f"mx{i}"),
            ]
            if numeric:
                e.append(sa.func.sum(col).label(f"s{i}"))
            return e

        def run(cols: list[tuple[int, str, bool]]) -> dict[str, dict]:
            exprs = [e for i, n, num in cols for e in exprs_for(i, n, num)]
            stmt = sa.select(*exprs).select_from(t)
            if where:
                stmt = stmt.where(sa.text(where))
            with self.engine.connect() as conn:
                row = conn.execute(stmt).mappings().one()
            res = {}
            for i, n, num in cols:
                stats = {"nulls": row[f"n{i}"], "distinct": row[f"d{i}"], "min": row[f"mn{i}"], "max": row[f"mx{i}"]}
                if num:
                    stats["sum"] = row[f"s{i}"]
                res[n] = stats
            return res

        indexed = [(i, n, num) for i, (n, num) in enumerate(columns)]
        try:
            return run(indexed)
        except Exception:
            # Some column types (CLOB, VARIANT, ...) do not support every aggregate;
            # retry column by column so one bad column does not hide the others.
            out: dict[str, dict] = {}
            for item in indexed:
                try:
                    out.update(run([item]))
                except Exception as exc:
                    out[item[1]] = {"error": str(exc).splitlines()[0][:200]}
            return out

    def stage(self, duck, name, table, columns, where=None, limit=None, order_by=None, upper_bound=None) -> int:
        types = {c.name: parse_type(c.native_type, self.dialect) for c in self.get_columns(table)}
        t = self._table(table, columns)
        stmt = sa.select(*[t.c[c] for c in columns])
        if where:
            stmt = stmt.where(sa.text(where))
        if upper_bound is not None:
            col, value, inclusive = upper_bound
            bound = sa.bindparam("ub", value)
            stmt = stmt.where(t.c[col] <= bound if inclusive else t.c[col] < bound)
        if order_by:
            stmt = stmt.order_by(*[t.c[c] for c in order_by])
        if limit:
            stmt = stmt.limit(limit)

        col_defs = ", ".join(f"{qi(c)} {duck_type(types[c])}" for c in columns)
        duck.execute(f"CREATE OR REPLACE TABLE {qi(name)} ({col_defs})")
        casts = ", ".join(f"CAST({qi(c)} AS {duck_type(types[c])})" for c in columns)
        total = 0
        with self.engine.connect().execution_options(stream_results=True) as conn:
            for chunk in pd.read_sql(stmt, conn, chunksize=CHUNK_ROWS):
                chunk.columns = columns
                # Normalise object columns (Decimal, LOBs, nested values) to text so DuckDB
                # can always ingest them; the CAST restores the declared type.
                for c in chunk.columns:
                    if chunk[c].dtype == object:
                        chunk[c] = chunk[c].map(
                            lambda v: (
                                None
                                if v is None
                                else (v.read() if hasattr(v, "read") else v if isinstance(v, (str, bytes)) else str(v))
                            )
                        )
                duck.register("_chunk", chunk)
                try:
                    duck.execute(f"INSERT INTO {qi(name)} SELECT {casts} FROM _chunk")
                finally:
                    duck.unregister("_chunk")
                total += len(chunk)
        return total

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()


# ---------------------------------------------------------------------------
# Concrete connectors
# ---------------------------------------------------------------------------


class OracleConnector(SQLAlchemyConnector):
    driver_module = "oracledb"
    spec = ConnectorSpec(
        type="oracle",
        label="Oracle",
        category="Database",
        dialect="oracle",
        driver_hint="oracledb",
        description="Oracle Database 12c+ (thin mode, no Oracle client needed)",
        fields=[
            ConfigField("host", "Host", required=True),
            ConfigField("port", "Port", "number", default=1521),
            ConfigField("service_name", "Service name", required=True, placeholder="ORCLPDB1"),
            ConfigField("username", "Username", required=True),
            ConfigField("password", "Password", "password", secret=True),
            ConfigField("schema", "Default schema (owner)", placeholder="SALES"),
        ],
    )

    def url(self):
        c = self.config
        return sa.URL.create(
            "oracle+oracledb",
            username=c.get("username"),
            password=c.get("password"),
            host=c.get("host"),
            port=int(c.get("port") or 1521),
            query={"service_name": c.get("service_name", "")},
        )

    def test_query(self) -> str:
        return "SELECT 1 FROM DUAL"


class SnowflakeConnector(SQLAlchemyConnector):
    driver_module = "snowflake.sqlalchemy"
    spec = ConnectorSpec(
        type="snowflake",
        label="Snowflake",
        category="Cloud warehouse",
        dialect="snowflake",
        driver_hint="snowflake-sqlalchemy",
        fields=[
            ConfigField("account", "Account identifier", required=True, placeholder="xy12345.eu-west-1"),
            ConfigField("username", "Username", required=True),
            ConfigField("password", "Password", "password", secret=True),
            ConfigField("warehouse", "Warehouse", required=True),
            ConfigField("database", "Database", required=True),
            ConfigField("schema", "Schema", default="PUBLIC"),
            ConfigField("role", "Role"),
        ],
    )

    def url(self):
        c = self.config
        query = {k: c[k] for k in ("warehouse", "role") if c.get(k)}
        return sa.URL.create(
            "snowflake",
            username=c.get("username"),
            password=c.get("password"),
            host=c.get("account"),
            database=f"{c.get('database')}/{c.get('schema') or 'PUBLIC'}",
            query=query,
        )


class RedshiftConnector(SQLAlchemyConnector):
    driver_module = "redshift_connector"
    spec = ConnectorSpec(
        type="redshift",
        label="Amazon Redshift",
        category="Cloud warehouse",
        dialect="redshift",
        driver_hint="sqlalchemy-redshift redshift_connector",
        fields=_host_fields(5439) + [ConfigField("schema", "Schema", default="public")],
    )

    def url(self):
        c = self.config
        return sa.URL.create(
            "redshift+redshift_connector",
            username=c.get("username"),
            password=c.get("password"),
            host=c.get("host"),
            port=int(c.get("port") or 5439),
            database=c.get("database"),
        )


class DatabricksConnector(SQLAlchemyConnector):
    driver_module = "databricks.sqlalchemy"
    spec = ConnectorSpec(
        type="databricks",
        label="Databricks (SQL warehouse / Delta)",
        category="Big data",
        dialect="databricks",
        driver_hint="databricks-sqlalchemy",
        description="Unity Catalog / Hive metastore tables through a SQL warehouse or cluster",
        fields=[
            ConfigField("host", "Server hostname", required=True, placeholder="adb-123.azuredatabricks.net"),
            ConfigField("http_path", "HTTP path", required=True, placeholder="/sql/1.0/warehouses/abc"),
            ConfigField("token", "Access token", "password", required=True, secret=True),
            ConfigField("catalog", "Catalog", default="hive_metastore"),
            ConfigField("schema", "Schema", default="default"),
        ],
    )

    def url(self):
        c = self.config
        return sa.URL.create(
            "databricks",
            username="token",
            password=c.get("token"),
            host=c.get("host"),
            query={
                "http_path": c.get("http_path", ""),
                "catalog": c.get("catalog") or "hive_metastore",
                "schema": c.get("schema") or "default",
            },
        )


class HiveConnector(SQLAlchemyConnector):
    driver_module = "pyhive"
    spec = ConnectorSpec(
        type="hive",
        label="Apache Hive / Spark Thrift",
        category="Big data",
        dialect="hive",
        driver_hint="'pyhive[hive_pure_sasl]'",
        description="HiveServer2 or Spark Thrift Server",
        fields=[
            ConfigField("host", "Host", required=True),
            ConfigField("port", "Port", "number", default=10000),
            ConfigField("database", "Database", default="default"),
            ConfigField("username", "Username"),
            ConfigField("password", "Password", "password", secret=True),
            ConfigField(
                "auth", "Authentication", "select", default="NONE", options=["NONE", "LDAP", "KERBEROS", "CUSTOM"]
            ),
            ConfigField("kerberos_service_name", "Kerberos service name", placeholder="hive"),
        ],
    )

    def url(self):
        c = self.config
        return sa.URL.create(
            "hive",
            username=c.get("username") or None,
            password=c.get("password") or None,
            host=c.get("host"),
            port=int(c.get("port") or 10000),
            database=c.get("database") or "default",
        )

    def engine_kwargs(self):
        c = self.config
        args: dict[str, Any] = {}
        if c.get("auth") and c["auth"] != "NONE":
            args["auth"] = c["auth"]
        if c.get("kerberos_service_name"):
            args["kerberos_service_name"] = c["kerberos_service_name"]
        return {"connect_args": args} if args else {}

    def default_schema(self):
        return None  # the database in the URL is the default


class PostgresConnector(SQLAlchemyConnector):
    driver_module = "psycopg2"
    spec = ConnectorSpec(
        type="postgres",
        label="PostgreSQL",
        category="Database",
        dialect="postgres",
        driver_hint="psycopg2-binary",
        fields=_host_fields(5432) + [ConfigField("schema", "Schema", default="public")],
    )

    def url(self):
        c = self.config
        return sa.URL.create(
            "postgresql+psycopg2",
            username=c.get("username"),
            password=c.get("password"),
            host=c.get("host"),
            port=int(c.get("port") or 5432),
            database=c.get("database"),
        )


class MySQLConnector(SQLAlchemyConnector):
    driver_module = "pymysql"
    spec = ConnectorSpec(
        type="mysql",
        label="MySQL / MariaDB",
        category="Database",
        dialect="mysql",
        driver_hint="pymysql",
        fields=_host_fields(3306),
    )

    def url(self):
        c = self.config
        return sa.URL.create(
            "mysql+pymysql",
            username=c.get("username"),
            password=c.get("password"),
            host=c.get("host"),
            port=int(c.get("port") or 3306),
            database=c.get("database"),
        )

    def default_schema(self):
        return None


class SQLServerConnector(SQLAlchemyConnector):
    driver_module = "pyodbc"
    spec = ConnectorSpec(
        type="sqlserver",
        label="SQL Server / Azure Synapse",
        category="Database",
        dialect="sqlserver",
        driver_hint="pyodbc (plus the Microsoft ODBC driver)",
        fields=_host_fields(1433)
        + [
            ConfigField("schema", "Schema", default="dbo"),
            ConfigField("odbc_driver", "ODBC driver", default="ODBC Driver 18 for SQL Server"),
        ],
    )

    def url(self):
        c = self.config
        return sa.URL.create(
            "mssql+pyodbc",
            username=c.get("username"),
            password=c.get("password"),
            host=c.get("host"),
            port=int(c.get("port") or 1433),
            database=c.get("database"),
            query={"driver": c.get("odbc_driver") or "ODBC Driver 18 for SQL Server", "TrustServerCertificate": "yes"},
        )


class TeradataConnector(SQLAlchemyConnector):
    driver_module = "teradatasqlalchemy"
    spec = ConnectorSpec(
        type="teradata",
        label="Teradata",
        category="Database",
        dialect="teradata",
        driver_hint="teradatasqlalchemy",
        fields=[
            ConfigField("host", "Host", required=True),
            ConfigField("username", "Username", required=True),
            ConfigField("password", "Password", "password", secret=True),
            ConfigField("schema", "Default database"),
        ],
    )

    def url(self):
        c = self.config
        return sa.URL.create("teradatasql", username=c.get("username"), password=c.get("password"), host=c.get("host"))


class BigQueryConnector(SQLAlchemyConnector):
    driver_module = "sqlalchemy_bigquery"
    spec = ConnectorSpec(
        type="bigquery",
        label="Google BigQuery",
        category="Cloud warehouse",
        dialect="bigquery",
        driver_hint="sqlalchemy-bigquery",
        fields=[
            ConfigField("project", "Project ID", required=True),
            ConfigField("schema", "Dataset", required=True),
            ConfigField(
                "credentials_json",
                "Service account JSON",
                "textarea",
                secret=True,
                help="Leave empty to use the runtime's default credentials",
            ),
        ],
    )

    def url(self):
        return f"bigquery://{self.config.get('project')}"

    def engine_kwargs(self):
        import json

        info = self.config.get("credentials_json")
        return {"credentials_info": json.loads(info)} if info else {}


class SQLiteConnector(SQLAlchemyConnector):
    spec = ConnectorSpec(
        type="sqlite",
        label="SQLite (local file)",
        category="Database",
        dialect="sqlite",
        description="Handy for demos and testing. Path is relative to the server's data folder.",
        fields=[ConfigField("path", "Database file", required=True, placeholder="demo/legacy_oracle.db")],
    )

    def url(self):
        root = get_settings().data_root
        path = (root / self.config.get("path", "")).resolve()
        if root not in path.parents and path != root:
            raise ConnectorError("SQLite file must be inside the server data folder")
        if not path.exists():
            raise ConnectorError(f"File not found: {self.config.get('path')}")
        return f"sqlite:///{Path(path)}"

    def default_schema(self):
        return None


class GenericSQLAlchemyConnector(SQLAlchemyConnector):
    spec = ConnectorSpec(
        type="sqlalchemy",
        label="Other (SQLAlchemy URL)",
        category="Database",
        dialect="generic",
        description="Any engine with a SQLAlchemy dialect: Synapse, Trino/Presto, Db2, Vertica, ...",
        fields=[
            ConfigField(
                "url",
                "SQLAlchemy URL",
                "password",
                required=True,
                secret=True,
                placeholder="trino://user@host:8080/hive",
            ),
            ConfigField("schema", "Default schema"),
            ConfigField(
                "dialect",
                "Type dialect",
                "select",
                default="generic",
                options=["generic", "oracle", "snowflake", "databricks", "hive", "redshift", "postgres"],
            ),
        ],
    )

    @property
    def dialect(self) -> str:
        return self.config.get("dialect") or "generic"

    def url(self):
        url = self.config.get("url", "")
        if url.startswith("sqlite"):
            raise ConnectorError("Use the SQLite connector for local files")
        return url
