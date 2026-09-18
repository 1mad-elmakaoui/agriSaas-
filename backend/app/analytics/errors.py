"""Database error extraction and classification.

Classification is not cosmetic. It decides whether a failed execution gets
another repair attempt at all: rewriting SQL never fixes ``permission denied``
or ``out of memory``, so those end the run instead of spending the remaining
attempts and three model calls on a guaranteed failure.

SQLSTATE is used in preference to message text because messages are localised
and change between major versions, while SQLSTATE is stable and specified.
"""

from __future__ import annotations

import re
from typing import Any

from app.analytics.execution_models import DbError, ErrorClass

# SQLSTATE -> classification. Exact codes first, then class prefixes.
_EXACT: dict[str, ErrorClass] = {
    "42601": ErrorClass.SYNTAX,             # syntax_error
    "42P01": ErrorClass.MISSING_OBJECT,     # undefined_table
    "42703": ErrorClass.MISSING_OBJECT,     # undefined_column
    "42883": ErrorClass.MISSING_OBJECT,     # undefined_function
    "42704": ErrorClass.MISSING_OBJECT,     # undefined_object
    "3F000": ErrorClass.MISSING_OBJECT,     # invalid_schema_name
    "42P02": ErrorClass.MISSING_OBJECT,     # undefined_parameter
    "42804": ErrorClass.TYPE_MISMATCH,      # datatype_mismatch
    "42846": ErrorClass.TYPE_MISMATCH,      # cannot_coerce
    "42P08": ErrorClass.TYPE_MISMATCH,      # ambiguous_parameter
    "22P02": ErrorClass.TYPE_MISMATCH,      # invalid_text_representation
    "22007": ErrorClass.TYPE_MISMATCH,      # invalid_datetime_format
    "42702": ErrorClass.AMBIGUOUS,          # ambiguous_column
    "42725": ErrorClass.AMBIGUOUS,          # ambiguous_function
    "42712": ErrorClass.AMBIGUOUS,          # duplicate_alias
    "42803": ErrorClass.GROUPING,           # grouping_error
    "42P20": ErrorClass.GROUPING,           # windowing_error
    "22012": ErrorClass.DIVISION_BY_ZERO,
    "42501": ErrorClass.PERMISSION,         # insufficient_privilege
    "25006": ErrorClass.READ_ONLY_VIOLATION,
    "57014": ErrorClass.TIMEOUT,            # query_canceled
    "55P03": ErrorClass.TIMEOUT,            # lock_not_available
    "40001": ErrorClass.TRANSIENT,          # serialization_failure
    "40P01": ErrorClass.TRANSIENT,          # deadlock_detected
    "57P01": ErrorClass.TRANSIENT,          # admin_shutdown
    "57P03": ErrorClass.TRANSIENT,          # cannot_connect_now
}

_BY_CLASS: dict[str, ErrorClass] = {
    "08": ErrorClass.TRANSIENT,   # connection exception
    "53": ErrorClass.RESOURCE,    # insufficient resources
    "54": ErrorClass.RESOURCE,    # program limit exceeded
    "58": ErrorClass.RESOURCE,    # system error
    "XX": ErrorClass.RESOURCE,    # internal error / data corrupted
    "0A": ErrorClass.SYNTAX,      # feature not supported
    "42": ErrorClass.SYNTAX,      # syntax/access rule violation, unclassified
    "22": ErrorClass.TYPE_MISMATCH,
    "25": ErrorClass.READ_ONLY_VIOLATION,
}

_TIMEOUT_PATTERNS = (
    "canceling statement due to statement timeout",
    "canceling statement due to user request",
    "query timeout",
    "timeout expired",
)
_READ_ONLY_PATTERNS = (
    "cannot execute",  # "cannot execute INSERT in a read-only transaction"
    "read-only transaction",
)


def classify_sqlstate(sqlstate: str | None, message: str = "") -> ErrorClass:
    """Classify by SQLSTATE, falling back to message inspection.

    Message inspection is a fallback only: it catches driver-level timeouts
    that never reach the server and therefore carry no SQLSTATE.
    """
    if sqlstate:
        exact = _EXACT.get(sqlstate.upper())
        if exact is not None:
            return exact
        by_class = _BY_CLASS.get(sqlstate[:2].upper())
        if by_class is not None:
            return by_class

    lowered = message.lower()
    if any(p in lowered for p in _TIMEOUT_PATTERNS):
        return ErrorClass.TIMEOUT
    if all(p in lowered for p in _READ_ONLY_PATTERNS):
        return ErrorClass.READ_ONLY_VIOLATION
    if "permission denied" in lowered:
        return ErrorClass.PERMISSION
    return ErrorClass.UNKNOWN


def _first_attr(obj: object, *names: str) -> Any:
    for name in names:
        value = getattr(obj, name, None)
        if value:
            return value
    return None


def _unwrap(exc: BaseException) -> BaseException:
    """Reach the driver exception through SQLAlchemy's wrapper.

    SQLAlchemy raises ``DBAPIError`` with the driver exception on ``.orig``;
    the useful fields (sqlstate, detail, hint, position) live there.
    """
    orig = getattr(exc, "orig", None)
    return orig if orig is not None else exc


def db_error_from_exception(exc: BaseException) -> DbError:
    """Build a :class:`DbError` from any driver or SQLAlchemy exception.

    ``DETAIL`` and ``HINT`` are carried through rather than discarded: they are
    frequently the parts that actually name the fix (*"Perhaps you meant to
    reference the column o.customer_id"*), and handing them to the repair node
    is worth more than the message alone.
    """
    driver_exc = _unwrap(exc)

    sqlstate = _first_attr(driver_exc, "sqlstate", "pgcode", "code")
    sqlstate = str(sqlstate) if sqlstate else None

    message = _first_attr(driver_exc, "message") or str(driver_exc) or repr(driver_exc)
    message = str(message).strip()

    detail = _first_attr(driver_exc, "detail", "detail_")
    hint = _first_attr(driver_exc, "hint", "hint_")

    position_raw = _first_attr(driver_exc, "position", "pos")
    position: int | None = None
    if position_raw is not None:
        try:
            position = int(position_raw)
        except (TypeError, ValueError):
            position = None

    # psycopg exposes these under .diag rather than as attributes.
    diag = getattr(driver_exc, "diag", None)
    if diag is not None:
        sqlstate = sqlstate or getattr(diag, "sqlstate", None)
        detail = detail or getattr(diag, "message_detail", None)
        hint = hint or getattr(diag, "message_hint", None)

    if isinstance(exc, TimeoutError) or isinstance(driver_exc, TimeoutError):
        classification = ErrorClass.TIMEOUT
    else:
        classification = classify_sqlstate(sqlstate, message)

    return DbError(
        message=_clean_message(message),
        sqlstate=sqlstate,
        detail=str(detail) if detail else None,
        hint=str(hint) if hint else None,
        position=position,
        classification=classification,
    )


_SQLALCHEMY_NOISE = re.compile(
    r"\s*\[SQL:.*|\s*\(Background on this error.*|^\(\w+\.\w+\)\s*", re.DOTALL
)

#: SQLAlchemy's asyncpg dialect re-raises through its own DBAPI shim, whose
#: ``str()`` is ``<class 'asyncpg.exceptions.UndefinedFunctionError'>: operator
#: does not exist: ...``. The class repr is noise in a repair prompt -- the
#: classification is already carried structurally in ``DbError.sqlstate`` -- and
#: it is the first thing the model reads.
_DRIVER_CLASS_PREFIX = re.compile(r"^<class '[^']+'>:\s*")


def _clean_message(message: str) -> str:
    """Strip SQLAlchemy's appended SQL echo, doc link, and driver class repr.

    The repair prompt already contains the SQL; repeating it inside the error
    doubles the token cost of every repair and buries the actual message.
    """
    cleaned = _SQLALCHEMY_NOISE.sub("", message)
    cleaned = _DRIVER_CLASS_PREFIX.sub("", cleaned)
    return cleaned.strip() or message.strip()


__all__ = ["classify_sqlstate", "db_error_from_exception"]
