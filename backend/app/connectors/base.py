"""Connector contract shared by every source / target system."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

import duckdb

FieldType = Literal["text", "password", "number", "boolean", "select", "textarea"]


@dataclass
class ConfigField:
    """A field of a connector's configuration form, rendered by the UI."""

    name: str
    label: str
    type: FieldType = "text"
    required: bool = False
    default: Any = None
    placeholder: str = ""
    help: str = ""
    options: list[str] = field(default_factory=list)
    secret: bool = False  # encrypted at rest, never returned by the API


@dataclass
class ConnectorSpec:
    type: str
    label: str
    category: Literal["Database", "Cloud warehouse", "Big data", "Files"]
    dialect: str
    fields: list[ConfigField]
    driver_hint: str = ""  # pip package needed for this connector
    description: str = ""


@dataclass
class ColumnInfo:
    name: str
    native_type: str
    nullable: bool = True


class ConnectorError(Exception):
    pass


class Connector(ABC):
    spec: ClassVar[ConnectorSpec]

    def __init__(self, config: dict[str, Any]):
        self.config = config

    @property
    def dialect(self) -> str:
        return self.spec.dialect

    @abstractmethod
    def test(self) -> None:
        """Raise ConnectorError when the connection does not work."""

    @abstractmethod
    def list_tables(self) -> list[str]: ...

    @abstractmethod
    def get_columns(self, table: str) -> list[ColumnInfo]: ...

    def get_primary_key(self, table: str) -> list[str]:
        return []

    @abstractmethod
    def count_rows(self, table: str, where: str | None = None) -> int: ...

    @abstractmethod
    def profile(self, table: str, columns: list[tuple[str, bool]], where: str | None = None) -> dict[str, dict]:
        """Column statistics computed inside the engine (pushdown).

        ``columns`` is a list of ``(name, is_numeric)``. Returns per column:
        ``nulls``, ``distinct``, ``min``, ``max`` and, for numeric columns, ``sum``.
        """

    @abstractmethod
    def stage(
        self,
        duck: duckdb.DuckDBPyConnection,
        name: str,
        table: str,
        columns: list[str],
        where: str | None = None,
        limit: int | None = None,
        order_by: list[str] | None = None,
        upper_bound: tuple[str, Any, bool] | None = None,
    ) -> int:
        """Copy rows into DuckDB table ``name``; returns the number of rows staged.

        ``upper_bound`` = ``(column, value, inclusive)`` keeps only rows with column <= value
        (or < value). Used to align the sampled key window of both sides.
        """

    def allowed_paths(self) -> list[str]:
        """Local/cloud paths DuckDB may read for this connector (file connectors)."""
        return []

    def prepare_duckdb(self, duck: duckdb.DuckDBPyConnection) -> None:
        """Load extensions / credentials before DuckDB external access is locked."""

    def close(self) -> None:
        pass
