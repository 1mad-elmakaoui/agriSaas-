"""Aggregation checks.

The brief asks for "GROUP BY covers all non-aggregated selected columns".
Implemented literally, that rule **rejects SQL PostgreSQL accepts**:

* Grouping by a table's primary key functionally determines every other column
  of that table (SQL:1999, implemented by PostgreSQL). ``SELECT c.id, c.name,
  count(*) FROM customers c GROUP BY c.id`` is valid.
* ``GROUP BY 1, 2`` refers to select-list positions, not columns.
* ``GROUPING SETS`` / ``ROLLUP`` / ``CUBE`` make coverage conditional.
* Grouping by an expression covers uses of the identical expression, which a
  naive name-based matcher will not equate.

So coverage is reported as a **warning**, and ``EXPLAIN`` -- which runs
PostgreSQL's own analyser -- is the authority that turns a genuine violation
into a rejection. A validator that rejects valid SQL is worse than one that
misses an error the next layer catches anyway.

Two things *are* hard errors here, because they are unambiguous and produce
much better messages before execution than after:

* an aggregate in ``WHERE`` (it belongs in ``HAVING``)
* a nested aggregate (``sum(count(*))``)
"""

from __future__ import annotations

from contextlib import suppress

from sqlglot import exp

from app.analytics.catalog import TableInfo
from app.analytics.validation.compat import as_expression
from app.analytics.validation.models import Severity, ValidationCode, ValidationIssue


def check_aggregation(
    root: exp.Expression, column_owner: dict[int, TableInfo]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for select in root.find_all(exp.Select):
        issues.extend(_check_aggregate_in_where(select))
        issues.extend(_check_nested_aggregates(select))
        issues.extend(_check_group_by_coverage(select, column_owner))
    return issues


def _check_aggregate_in_where(select: exp.Select) -> list[ValidationIssue]:
    where = select.args.get("where")
    if where is None:
        return []
    aggregates = [
        node for node in where.find_all(exp.AggFunc) if not _inside_subquery(node, where)
    ]
    if not aggregates:
        return []
    return [
        ValidationIssue(
            code=ValidationCode.AGGREGATE_IN_WHERE,
            severity=Severity.ERROR,
            message=(
                "Aggregate functions are not allowed in WHERE; WHERE is evaluated "
                "before grouping."
            ),
            fragment=aggregates[0].sql(dialect="postgres"),
            suggestion="Move the aggregate condition into a HAVING clause.",
        )
    ]


def _check_nested_aggregates(select: exp.Select) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for aggregate in select.find_all(exp.AggFunc):
        for inner in aggregate.find_all(exp.AggFunc):
            if inner is aggregate:
                continue
            # A window function wrapping an aggregate is legal; only a plain
            # aggregate directly inside another is not.
            if inner.find_ancestor(exp.Window) is not None:
                continue
            issues.append(
                ValidationIssue(
                    code=ValidationCode.NESTED_AGGREGATE,
                    severity=Severity.ERROR,
                    message="Aggregate functions cannot be nested inside one another.",
                    fragment=aggregate.sql(dialect="postgres"),
                    suggestion=(
                        "Compute the inner aggregate in a subquery or CTE, then "
                        "aggregate its result."
                    ),
                )
            )
            break
    return issues


def _check_group_by_coverage(
    select: exp.Select, column_owner: dict[int, TableInfo]
) -> list[ValidationIssue]:
    group = select.args.get("group")
    projections = select.expressions
    has_aggregate = any(
        _is_own_aggregate(node, select)
        for projection in projections
        for node in projection.find_all(exp.AggFunc)
    )

    if group is None:
        # No GROUP BY: mixing an aggregate with a bare column is an error, but
        # PostgreSQL reports it precisely and the message is good. Flag it as a
        # warning so the repair prompt has it early.
        if has_aggregate:
            bare = _bare_columns(projections, select)
            if bare:
                return [
                    ValidationIssue(
                        code=ValidationCode.GROUP_BY_MISMATCH,
                        severity=Severity.WARNING,
                        message=(
                            "The select list mixes aggregates with non-aggregated columns "
                            f"({', '.join(sorted(bare))}) but has no GROUP BY."
                        ),
                        suggestion=(
                            "Add a GROUP BY listing the non-aggregated columns, or "
                            "aggregate them too."
                        ),
                    )
                ]
        return []

    # GROUPING SETS / ROLLUP / CUBE: coverage is conditional per grouping set.
    # Modelling that correctly is not worth it when EXPLAIN decides anyway.
    if group.args.get("grouping_sets") or group.args.get("rollup") or group.args.get("cube"):
        return []

    grouped_sql: set[str] = set()
    grouped_ordinals: set[int] = set()
    grouped_pk_tables: set[str] = set()

    for item in group.expressions:
        if isinstance(item, exp.Literal) and not item.is_string:
            # A non-integer numeric literal in GROUP BY is legal but is not an
            # ordinal, so it contributes no coverage and is simply skipped.
            with suppress(TypeError, ValueError):
                grouped_ordinals.add(int(item.name))
            continue
        grouped_sql.add(_normalize(item))
        owner = column_owner.get(id(item)) if isinstance(item, exp.Column) else None
        # Grouping by the whole primary key functionally determines every other
        # column of that table.
        if (
            owner is not None
            and isinstance(item, exp.Column)
            and _covers_primary_key(owner, group.expressions, column_owner)
        ):
            grouped_pk_tables.add(owner.ref.qualified)

    uncovered: list[str] = []
    for position, projection in enumerate(projections, start=1):
        target = projection.this if isinstance(projection, exp.Alias) else projection
        if _contains_own_aggregate(target, select):
            continue
        if position in grouped_ordinals:
            continue
        if _normalize(target) in grouped_sql:
            continue
        if isinstance(target, exp.Column):
            owner = column_owner.get(id(target))
            if owner is not None and owner.ref.qualified in grouped_pk_tables:
                continue
        if isinstance(target, (exp.Literal, exp.Null, exp.Boolean)):
            continue
        if _is_constant_expression(target):
            continue
        uncovered.append(target.sql(dialect="postgres"))

    if not uncovered:
        return []

    return [
        ValidationIssue(
            code=ValidationCode.GROUP_BY_MISMATCH,
            severity=Severity.WARNING,
            message=(
                "These selected expressions are neither aggregated nor covered by "
                f"GROUP BY: {', '.join(uncovered)}. PostgreSQL may still accept this "
                "if a grouped column functionally determines them."
            ),
            suggestion=(
                "Add them to GROUP BY, wrap them in an aggregate, or group by the "
                "table's primary key."
            ),
        )
    ]


def _covers_primary_key(
    table: TableInfo,
    group_items: list[exp.Expression],
    column_owner: dict[int, TableInfo],
) -> bool:
    """Whether the GROUP BY includes every primary-key column of ``table``."""
    if not table.primary_key:
        return False
    grouped_names = {
        item.name.lower()
        for item in group_items
        if isinstance(item, exp.Column)
        and column_owner.get(id(item)) is not None
        and column_owner[id(item)].ref == table.ref
    }
    return {c.lower() for c in table.primary_key} <= grouped_names


def _bare_columns(projections: list[exp.Expression], select: exp.Select) -> set[str]:
    bare: set[str] = set()
    for projection in projections:
        target = projection.this if isinstance(projection, exp.Alias) else projection
        if _contains_own_aggregate(target, select):
            continue
        for column in target.find_all(exp.Column):
            if _inside_subquery(column, select):
                continue
            bare.add(column.sql(dialect="postgres"))
    return bare


def _is_own_aggregate(node: exp.AggFunc, select: exp.Select) -> bool:
    """Whether an aggregate belongs to ``select`` rather than a nested query.

    A window function is not an aggregation of the outer query -- ``sum(x) OVER
    (...)`` does not require a GROUP BY -- so it does not count either.
    """
    if _inside_subquery(as_expression(node), select):
        return False
    return node.find_ancestor(exp.Window) is None


def _contains_own_aggregate(node: exp.Expression, select: exp.Select) -> bool:
    return any(_is_own_aggregate(agg, select) for agg in node.find_all(exp.AggFunc))


def _inside_subquery(node: exp.Expression, boundary: exp.Expression) -> bool:
    """Whether ``node`` sits inside a nested SELECT below ``boundary``."""
    current = node.parent
    while current is not None and current is not boundary:
        if isinstance(current, (exp.Subquery, exp.Select)) and current is not boundary:
            return True
        current = current.parent
    return False


def _is_constant_expression(node: exp.Expression) -> bool:
    """True for expressions with no column references (literals, now(), …)."""
    return not any(True for _ in node.find_all(exp.Column))


def _normalize(node: exp.Expression) -> str:
    """Comparison key for GROUP BY matching.

    Generated SQL with normalised identifiers, so ``o.order_date`` and
    ``O.Order_Date`` compare equal while a genuinely different expression does
    not.
    """
    try:
        return node.sql(dialect="postgres", normalize=True, comments=False).lower()
    except Exception:
        return str(node).lower()


__all__ = ["check_aggregation"]
