"""Statement-class enforcement -- the security gate.

Everything here is rejected at the **AST level**, never by string matching. A
denylist over SQL text is trivially defeated (``DEL/**/ETE``, casing, unicode
escapes, a comment in the middle of a keyword) and produces false positives on
harmless data (a column literally named ``update_type``).

This stage fails **closed**. A statement sqlglot could not classify -- which it
surfaces as ``exp.Command`` -- is rejected rather than assumed benign. That
matters: ``EXPLAIN``, ``DO $$ … $$``, ``CALL proc()``, and ``VACUUM`` all parse
as ``Command`` on this dialect, and treating an unclassifiable statement as
safe would be exactly the wrong default.

A finding here routes to *reject*, never to repair. If the model emitted a
``DELETE``, asking it to try again is the wrong response -- that is a security
event to surface, not a syntax error to fix.
"""

from __future__ import annotations

from sqlglot import exp

from app.analytics.validation.compat import parent_of
from app.analytics.validation.models import (
    Severity,
    ValidationCode,
    ValidationIssue,
)

#: Node types that modify data, schema, or permissions. Presence anywhere in
#: the tree -- including inside a CTE or a subquery -- is fatal.
#:
#: ``WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d`` is the case that
#: makes a root-node check insufficient: sqlglot parses it as a *Select* whose
#: ``with`` argument contains a Delete. A validator that only inspected the
#: root would pass a data-modifying statement.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Alter,
    exp.Create,
    exp.TruncateTable,
    exp.Merge,
    exp.Grant,
    exp.Revoke,
    exp.Copy,
    exp.Command,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Use,
    exp.Set,
)

_NODE_LABEL: dict[type[exp.Expression], str] = {
    exp.Insert: "INSERT",
    exp.Update: "UPDATE",
    exp.Delete: "DELETE",
    exp.Drop: "DROP",
    exp.Alter: "ALTER",
    exp.Create: "CREATE",
    exp.TruncateTable: "TRUNCATE",
    exp.Merge: "MERGE",
    exp.Grant: "GRANT",
    exp.Revoke: "REVOKE",
    exp.Copy: "COPY",
    exp.Transaction: "BEGIN/START TRANSACTION",
    exp.Commit: "COMMIT",
    exp.Rollback: "ROLLBACK",
    exp.Use: "USE",
    exp.Set: "SET",
}

#: Root node types that constitute a read-only query.
ALLOWED_ROOTS: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.Union,
    exp.Except,
    exp.Intersect,
    exp.Subquery,
)


def check_statement_class(root: exp.Expression) -> list[ValidationIssue]:
    """Reject anything that is not a single read-only query."""
    issues: list[ValidationIssue] = []

    if not isinstance(root, ALLOWED_ROOTS):
        issues.append(
            ValidationIssue(
                code=(
                    ValidationCode.FORBIDDEN_STATEMENT
                    if isinstance(root, FORBIDDEN_NODES)
                    else ValidationCode.NOT_A_SELECT
                ),
                severity=Severity.ERROR,
                message=(
                    f"Only read-only SELECT (or WITH ... SELECT) statements are permitted; "
                    f"got {_describe(root)}."
                ),
                fragment=_fragment(root),
                suggestion="Rewrite the request as a SELECT that reads the data instead.",
            )
        )
        # No point auditing the interior of a statement that is already fatal.
        return issues

    issues.extend(_check_nested_dml(root))
    issues.extend(_check_select_into(root))
    issues.extend(_check_locking(root))
    return issues


def _check_nested_dml(root: exp.Expression) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for node in root.walk():
        if node is root or not isinstance(node, FORBIDDEN_NODES):
            continue
        label = _NODE_LABEL.get(type(node), type(node).__name__.upper())
        inside_cte = any(isinstance(a, exp.CTE) for a in _ancestors(node))
        issues.append(
            ValidationIssue(
                code=(
                    ValidationCode.CTE_WITH_DML
                    if inside_cte
                    else ValidationCode.FORBIDDEN_STATEMENT
                ),
                severity=Severity.ERROR,
                message=(
                    f"{label} found inside "
                    f"{'a common table expression' if inside_cte else 'the query'}. "
                    "Data-modifying statements are not permitted anywhere in the tree."
                ),
                fragment=_fragment(node),
            )
        )
    return issues


def _check_select_into(root: exp.Expression) -> list[ValidationIssue]:
    """``SELECT ... INTO newtable`` creates a table. It is DDL wearing a SELECT."""
    issues: list[ValidationIssue] = []
    for node in root.find_all(exp.Select):
        into = node.args.get("into")
        if into is not None:
            issues.append(
                ValidationIssue(
                    code=ValidationCode.SELECT_INTO,
                    severity=Severity.ERROR,
                    message=(
                        "SELECT ... INTO creates a new table and is not permitted. "
                        "It is a data-definition statement despite the SELECT keyword."
                    ),
                    fragment=_fragment(into),
                )
            )
    return issues


def _check_locking(root: exp.Expression) -> list[ValidationIssue]:
    """``FOR UPDATE`` / ``FOR SHARE`` take row locks.

    They also fail outright in a read-only transaction, but catching them here
    turns an opaque runtime error into a specific message the repair node can
    act on -- and keeps the rejection at the layer that owns the policy.
    """
    issues: list[ValidationIssue] = []
    for node in root.find_all(exp.Select):
        locks = node.args.get("locks")
        if locks:
            issues.append(
                ValidationIssue(
                    code=ValidationCode.LOCKING_CLAUSE,
                    severity=Severity.ERROR,
                    message=(
                        "Row-locking clauses (FOR UPDATE / FOR SHARE / FOR NO KEY UPDATE) "
                        "are not permitted in a read-only query."
                    ),
                    fragment="; ".join(lock.sql(dialect="postgres") for lock in locks),
                    suggestion="Remove the locking clause.",
                )
            )
    return issues


def _ancestors(node: exp.Expression) -> list[exp.Expression]:
    out: list[exp.Expression] = []
    current = parent_of(node)
    while current is not None:
        out.append(current)
        current = parent_of(current)
    return out


def _describe(node: exp.Expression) -> str:
    if isinstance(node, exp.Command):
        # Command is sqlglot's "I could not parse this" fallback. Naming it as
        # unrecognised is more honest than guessing what it was.
        raw = str(node.this) if node.this else "unrecognised statement"
        return f"an unrecognised or unsupported statement ({raw.upper()})"
    return _NODE_LABEL.get(type(node), type(node).__name__.upper())


def _fragment(node: exp.Expression, limit: int = 200) -> str:
    try:
        text = node.sql(dialect="postgres")
    except Exception:
        text = str(node)
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["ALLOWED_ROOTS", "FORBIDDEN_NODES", "check_statement_class"]
