"""Projection rules and LIMIT injection.

Two things happen here.

``SELECT *`` is rejected. It is not a safety matter -- it is a correctness and
cost one. A star projection makes the result set depend on column order and on
future DDL, defeats the byte budget's predictability, and makes the explanation
node describe columns the user never asked about.

LIMIT injection enforces "always apply a LIMIT unless the user explicitly
requests all rows". Three rules that are easy to get wrong:

1. Inject at the **outermost** query only. A LIMIT pushed into a CTE changes
   the query's meaning -- ``WITH t AS (SELECT … LIMIT 500) SELECT count(*)
   FROM t`` answers a different question than the user asked.
2. An existing LIMIT is **lowered**, never raised. A user asking for 10 rows
   must not receive 500.
3. Injection is skipped when the user explicitly asked for everything, and the
   row cap in the executor still applies -- so "all rows" means "all rows up to
   the hard transport limit", not "unbounded".
"""

from __future__ import annotations

from sqlglot import exp

from app.analytics.validation.models import Severity, ValidationCode, ValidationIssue


def check_projection(
    root: exp.Expression, *, allow_select_star: bool
) -> list[ValidationIssue]:
    """Reject ``SELECT *`` and ``SELECT t.*``."""
    if allow_select_star:
        return []

    issues: list[ValidationIssue] = []
    for select in root.find_all(exp.Select):
        for projection in select.expressions:
            if isinstance(projection, exp.Star):
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.SELECT_STAR,
                        severity=Severity.ERROR,
                        message=(
                            "SELECT * is not permitted. List the columns the question "
                            "actually needs."
                        ),
                        fragment=_context(select),
                        suggestion="Replace * with the specific columns required.",
                    )
                )
            elif isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star):
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.SELECT_STAR,
                        severity=Severity.ERROR,
                        message=(
                            f"{projection.sql(dialect='postgres')} is not permitted. "
                            "List the columns the question actually needs."
                        ),
                        fragment=_context(select),
                        suggestion="Replace the star with the specific columns required.",
                    )
                )
    return issues


class LimitOutcome:
    """Result of the LIMIT stage."""

    __slots__ = ("effective_limit", "expression", "injected", "issues")

    def __init__(
        self,
        expression: exp.Expression,
        *,
        injected: bool,
        effective_limit: int | None,
        issues: list[ValidationIssue],
    ) -> None:
        self.expression = expression
        self.injected = injected
        self.effective_limit = effective_limit
        self.issues = issues


def apply_limit(
    root: exp.Expression,
    *,
    row_limit: int,
    wants_all_rows: bool,
    require_limit: bool,
) -> LimitOutcome:
    """Ensure the outermost query is bounded.

    Mutates a copy; the caller keeps the original for reporting.
    """
    expression = root.copy()
    issues: list[ValidationIssue] = []

    if wants_all_rows:
        # The executor's row cap and byte budget still apply. "All rows" means
        # "everything up to the hard transport limit", and the response says so
        # via QueryResult.truncated rather than silently returning a subset.
        return LimitOutcome(expression, injected=False, effective_limit=None, issues=issues)

    existing = _outermost_limit(expression)

    if existing is None:
        if not require_limit:
            return LimitOutcome(
                expression, injected=False, effective_limit=None, issues=issues
            )
        _set_limit(expression, row_limit)
        return LimitOutcome(
            expression, injected=True, effective_limit=row_limit, issues=issues
        )

    current = _limit_value(existing)
    if current is None:
        # A parameterised or expression LIMIT -- leave it alone rather than
        # guessing, and let the executor's row cap bound the transfer.
        return LimitOutcome(expression, injected=False, effective_limit=None, issues=issues)

    if current > row_limit:
        _set_limit(expression, row_limit)
        return LimitOutcome(
            expression, injected=True, effective_limit=row_limit, issues=issues
        )

    return LimitOutcome(expression, injected=False, effective_limit=current, issues=issues)


def _outermost_limit(expression: exp.Expression) -> exp.Limit | None:
    """The LIMIT belonging to the top-level query, ignoring nested ones."""
    limit = expression.args.get("limit")
    return limit if isinstance(limit, exp.Limit) else None


def _limit_value(limit: exp.Limit) -> int | None:
    target = limit.expression
    if isinstance(target, exp.Literal) and not target.is_string:
        try:
            return int(target.name)
        except (TypeError, ValueError):
            return None
    return None


def _set_limit(expression: exp.Expression, value: int) -> None:
    expression.set(
        "limit",
        exp.Limit(expression=exp.Literal.number(value)),
    )


def _context(select: exp.Select, limit: int = 120) -> str:
    try:
        text = select.sql(dialect="postgres")
    except Exception:
        text = str(select)
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["LimitOutcome", "apply_limit", "check_projection"]
