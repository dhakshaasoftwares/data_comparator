"""Connector registry."""

from __future__ import annotations

from typing import Any

from .base import ColumnInfo, Connector, ConnectorError, ConnectorSpec
from .files import FilesConnector, sandboxed_duckdb
from .sql import (
    BigQueryConnector,
    DatabricksConnector,
    GenericSQLAlchemyConnector,
    HiveConnector,
    MySQLConnector,
    OracleConnector,
    PostgresConnector,
    RedshiftConnector,
    SnowflakeConnector,
    SQLiteConnector,
    SQLServerConnector,
    TeradataConnector,
)

CONNECTORS: dict[str, type[Connector]] = {
    c.spec.type: c
    for c in (
        OracleConnector,
        SnowflakeConnector,
        DatabricksConnector,
        RedshiftConnector,
        HiveConnector,
        FilesConnector,
        BigQueryConnector,
        TeradataConnector,
        SQLServerConnector,
        PostgresConnector,
        MySQLConnector,
        SQLiteConnector,
        GenericSQLAlchemyConnector,
    )
}


def get_connector(type_: str, config: dict[str, Any]) -> Connector:
    try:
        cls = CONNECTORS[type_]
    except KeyError:
        raise ConnectorError(f"Unknown connector type '{type_}'") from None
    return cls(config)


def secret_fields(type_: str) -> set[str]:
    cls = CONNECTORS.get(type_)
    return {f.name for f in cls.spec.fields if f.secret} if cls else set()


__all__ = [
    "CONNECTORS",
    "ColumnInfo",
    "Connector",
    "ConnectorError",
    "ConnectorSpec",
    "get_connector",
    "sandboxed_duckdb",
    "secret_fields",
]
