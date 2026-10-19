"""Comparison job configuration.

This is the single document the UI edits. It is stored as JSON, so the same
config can be exported to Git or run from the CLI / CI pipelines.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

RuleType = Literal["not_null", "unique", "range", "regex", "allowed_values", "max_length", "expression"]


class ColumnMapping(BaseModel):
    source: str
    target: str


class QualityRule(BaseModel):
    type: RuleType
    column: str | None = None
    columns: list[str] = Field(default_factory=list)  # for `unique` on composite columns
    min: float | None = None
    max: float | None = None
    pattern: str | None = None
    values: list[Any] = Field(default_factory=list)
    length: int | None = None
    expression: str | None = None  # SQL boolean expression, true for valid rows
    side: Literal["source", "target", "both"] = "target"
    severity: Literal["low", "medium", "high"] = "medium"
    max_violation_pct: float = 0.0  # tolerated share of bad rows (0-100)
    name: str | None = None

    @model_validator(mode="after")
    def _check(self) -> QualityRule:
        needs_column = {"not_null", "range", "regex", "allowed_values", "max_length"}
        if self.type in needs_column and not self.column:
            raise ValueError(f"Rule '{self.type}' requires a column")
        if self.type == "unique" and not (self.column or self.columns):
            raise ValueError("Rule 'unique' requires a column or columns")
        if self.type == "expression" and not self.expression:
            raise ValueError("Rule 'expression' requires an expression")
        if self.type == "regex" and not self.pattern:
            raise ValueError("Rule 'regex' requires a pattern")
        return self

    def label(self) -> str:
        if self.name:
            return self.name
        target = self.column or ", ".join(self.columns) or self.expression or ""
        return f"{self.type}({target})"


class TablePair(BaseModel):
    source_table: str
    target_table: str
    key_columns: list[str] = Field(default_factory=list)  # source-side names
    column_mappings: list[ColumnMapping] = Field(default_factory=list)  # overrides of auto mapping
    exclude_columns: list[str] = Field(default_factory=list)  # source-side names
    source_filter: str | None = None  # SQL WHERE clause pushed to the source
    target_filter: str | None = None
    rules: list[QualityRule] = Field(default_factory=list)


class Normalization(BaseModel):
    trim_strings: bool = True
    case_insensitive: bool = False
    null_equals_empty: bool = True
    numeric_tolerance: float = 0.0  # absolute tolerance
    timestamp_precision: Literal["second", "millisecond", "microsecond"] = "microsecond"


class ScoreWeights(BaseModel):
    completeness: float = 30
    accuracy: float = 35
    schema_: float = Field(20, alias="schema")
    quality: float = 15

    model_config = {"populate_by_name": True}


class Thresholds(BaseModel):
    pass_score: float = 95
    warn_score: float = 80


class JobOptions(BaseModel):
    # counts  : schema + row counts only (pushdown, no data movement)
    # profile : + column statistics pushed down to each engine
    # full    : + row-level diff by key (data staged in a local DuckDB engine)
    mode: Literal["counts", "profile", "full"] = "full"
    max_rows: int | None = 1_000_000  # per table and side for staged comparisons
    sample_size: int = 20  # mismatch examples kept per category
    normalization: Normalization = Field(default_factory=Normalization)
    weights: ScoreWeights = Field(default_factory=ScoreWeights)
    thresholds: Thresholds = Field(default_factory=Thresholds)


class JobConfig(BaseModel):
    source_connection_id: int
    target_connection_id: int
    tables: list[TablePair] = Field(min_length=1)
    options: JobOptions = Field(default_factory=JobOptions)
