"""Execution results and database error classification.

:class:`ErrorClass` is load-bearing. It decides whether a failed execution is
worth another repair attempt at all: no amount of SQL rewriting fixes a
permission error or an out-of-memory, so those terminate the run immediately
instead of consuming the attempt budget and three model calls.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorClass(StrEnum):
    SYNTAX = "syntax"
    MISSING_OBJECT = "missing_object"
    TYPE_MISMATCH = "type_mismatch"
    AMBIGUOUS = "ambiguous"
    GROUPING = "grouping"
    DIVISION_BY_ZERO = "division_by_zero"
    PERMISSION = "permission"
    READ_ONLY_VIOLATION = "read_only_violation"
    TIMEOUT = "timeout"
    RESOURCE = "resource"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"


#: Classes that no rewrite of the SQL can fix. Reaching one of these ends the
#: run rather than spending the remaining attempts.
#:
#: TIMEOUT is deliberately *not* here: a timeout is often a query that needs a
#: narrower filter, which is exactly what the repair node can do.
UNRECOVERABLE_CLASSES: frozenset[ErrorClass] = frozenset(
    {
        ErrorClass.PERMISSION,
        ErrorClass.RESOURCE,
        ErrorClass.READ_ONLY_VIOLATION,
    }
)


class DbError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message: str
    sqlstate: str | None = None
    detail: str | None = None
    hint: str | None = None
    position: int | None = None
    classification: ErrorClass = ErrorClass.UNKNOWN

    @property
    def is_recoverable(self) -> bool:
        return self.classification not in UNRECOVERABLE_CLASSES

    def render(self) -> str:
        """Full error text for the repair prompt.

        PostgreSQL's ``DETAIL`` and ``HINT`` are frequently the parts that
        actually name the fix (``Perhaps you meant to reference the column
        "o.customer_id"``), so they are included rather than discarded.
        """
        parts = [self.message]
        if self.sqlstate:
            parts.append(f"SQLSTATE: {self.sqlstate}")
        if self.detail:
            parts.append(f"DETAIL: {self.detail}")
        if self.hint:
            parts.append(f"HINT: {self.hint}")
        if self.position is not None:
            parts.append(f"POSITION: {self.position}")
        return "\n".join(parts)


class ResultColumn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type_name: str | None = None


class QueryResult(BaseModel):
    """Rows returned to the caller.

    ``truncated`` is set when either the row cap or the byte budget stopped the
    fetch. The summary node is told about it explicitly, because describing a
    truncated result as if it were complete is a correctness failure, not a
    presentation one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    columns: tuple[ResultColumn, ...] = ()
    rows: tuple[dict[str, Any], ...] = ()
    row_count: int = Field(default=0, ge=0)
    truncated: bool = False
    truncation_reason: str | None = None
    bytes_returned: int = Field(default=0, ge=0)
    execution_time_ms: int = Field(default=0, ge=0)

    @property
    def is_empty(self) -> bool:
        return self.row_count == 0

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


__all__ = [
    "UNRECOVERABLE_CLASSES",
    "DbError",
    "ErrorClass",
    "QueryResult",
    "ResultColumn",
]
