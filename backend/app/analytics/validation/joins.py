"""Join validation.

The brief asks that "join conditions reference real foreign keys or valid
column pairs". Enforced as *reject unless FK-backed*, that breaks correct
queries: analytics warehouses routinely drop FK constraints for load
performance, and legitimate joins exist that no FK could describe -- date
dimensions, business keys, self-joins, range joins.

So three outcomes rather than two:

===========================  =========  ==========================================
Join predicate               Outcome    Rationale
===========================  =========  ==========================================
Backed by a declared FK      pass       Provably a real relationship.
Type-compatible, no FK       warning    Plausible; the schema simply does not say.
Type-incompatible            error      ``orders.total = customers.name`` is wrong
                                        under any schema.
===========================  =========  ==========================================

``strict_joins`` promotes the warning to an error for deployments that do
declare their foreign keys. It defaults to off, because rejecting every
non-FK join by default would break more valid queries than it catches invalid
ones.

A ``JOIN`` with no ``ON`` at all is a different matter: it is a Cartesian
product, almost always a mistake, and cheap to catch here rather than after it
has multiplied two large tables together.
"""

from __future__ import annotations

from sqlglot import exp

from app.analytics.catalog import CatalogSnapshot, ColumnRef, TableInfo, types_compatible
from app.analytics.validation.models import Severity, ValidationCode, ValidationIssue


def check_joins(
    root: exp.Expression,
    catalog: CatalogSnapshot,
    column_owner: dict[int, TableInfo],
    *,
    strict: bool = False,
    max_joins: int = 12,
    reject_explicit_cross_join: bool = True,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    joins = list(root.find_all(exp.Join))

    if len(joins) > max_joins:
        issues.append(
            ValidationIssue(
                code=ValidationCode.TOO_MANY_JOINS,
                severity=Severity.ERROR,
                message=(
                    f"Query contains {len(joins)} joins, above the configured maximum "
                    f"of {max_joins}."
                ),
                suggestion="Simplify the query or split it into stages using CTEs.",
            )
        )

    for join in joins:
        issues.extend(
            _check_join(
                join,
                catalog,
                column_owner,
                strict=strict,
                reject_explicit_cross_join=reject_explicit_cross_join,
            )
        )
    return issues


def _check_join(
    join: exp.Join,
    catalog: CatalogSnapshot,
    column_owner: dict[int, TableInfo],
    *,
    strict: bool,
    reject_explicit_cross_join: bool = True,
) -> list[ValidationIssue]:
    on_clause = join.args.get("on")
    using = join.args.get("using")

    if on_clause is None and not using:
        if _is_intentional_cross_join(
            join, reject_explicit_cross_join=reject_explicit_cross_join
        ):
            return []
        return [
            ValidationIssue(
                code=ValidationCode.CARTESIAN_JOIN,
                severity=Severity.ERROR,
                message=(
                    "JOIN without an ON or USING condition produces a Cartesian "
                    "product of both tables."
                ),
                fragment=_fragment(join),
                suggestion=(
                    "Add an ON condition linking the tables, or write CROSS JOIN if "
                    "the Cartesian product is genuinely intended."
                ),
            )
        ]

    if on_clause is None:
        # USING (col) is by definition an equality on identically named
        # columns; there is nothing further to verify here.
        return []

    issues: list[ValidationIssue] = []
    equalities = _equality_pairs(on_clause, column_owner)

    if not equalities and not _has_any_column_predicate(on_clause):
        issues.append(
            ValidationIssue(
                code=ValidationCode.CARTESIAN_JOIN,
                severity=Severity.WARNING,
                message=(
                    "The ON clause does not relate columns from the two sides of the "
                    "join, so it does not constrain the result."
                ),
                fragment=_fragment(on_clause),
            )
        )

    for left_ref, right_ref, left_table, right_table, fragment in equalities:
        if left_table.ref == right_table.ref:
            continue  # self-join; there is no FK to expect

        left_column = left_table.column(left_ref.column)
        right_column = right_table.column(right_ref.column)
        if left_column is None or right_column is None:
            continue  # already reported by the resolver

        if not types_compatible(left_column.category, right_column.category):
            issues.append(
                ValidationIssue(
                    code=ValidationCode.TYPE_INCOMPATIBLE_JOIN,
                    severity=Severity.ERROR,
                    message=(
                        f"Join compares {left_ref.qualified} ({left_column.data_type}) "
                        f"with {right_ref.qualified} ({right_column.data_type}); these "
                        "types are not comparable."
                    ),
                    fragment=fragment,
                    suggestion=_suggest_fk_path(catalog, left_table, right_table),
                )
            )
            continue

        if catalog.foreign_key_between(left_ref, right_ref) is not None:
            continue

        issues.append(
            ValidationIssue(
                code=ValidationCode.NON_FK_JOIN,
                severity=Severity.ERROR if strict else Severity.WARNING,
                message=(
                    f"Join between {left_ref.qualified} and {right_ref.qualified} is not "
                    "backed by a declared foreign key."
                ),
                fragment=fragment,
                suggestion=_suggest_fk_path(catalog, left_table, right_table),
            )
        )

    return issues


def _equality_pairs(
    on_clause: exp.Expression, column_owner: dict[int, TableInfo]
) -> list[tuple[ColumnRef, ColumnRef, TableInfo, TableInfo, str]]:
    """Column-to-column equalities in an ON clause.

    Only equalities between two *resolved* columns are considered. A predicate
    like ``o.status = 'paid'`` is a filter, not a relationship, and has nothing
    to check.
    """
    pairs = []
    for equality in on_clause.find_all(exp.EQ):
        left, right = equality.this, equality.expression
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue
        left_table = column_owner.get(id(left))
        right_table = column_owner.get(id(right))
        if left_table is None or right_table is None:
            continue
        pairs.append(
            (
                ColumnRef(
                    schema_name=left_table.ref.schema_name,
                    table=left_table.ref.table,
                    column=left.name,
                ),
                ColumnRef(
                    schema_name=right_table.ref.schema_name,
                    table=right_table.ref.table,
                    column=right.name,
                ),
                left_table,
                right_table,
                equality.sql(dialect="postgres"),
            )
        )
    return pairs


def _has_any_column_predicate(on_clause: exp.Expression) -> bool:
    return any(True for _ in on_clause.find_all(exp.Column))


def _is_intentional_cross_join(
    join: exp.Join, *, reject_explicit_cross_join: bool = True
) -> bool:
    """Une jointure sans condition est-elle voulue ?

    `LATERAL` et les fonctions de table le sont toujours : ce sont des
    constructions dont l'absence de `ON` est la définition même.

    `CROSS JOIN` dépend de qui écrit. Voir `ValidationSettings.
    reject_explicit_cross_join` : ici l'auteur est un modèle, et le mot-clé ne
    prouve donc aucune intention.
    """
    kind = (join.args.get("kind") or "").upper()
    side = (join.args.get("side") or "").upper()
    if kind == "CROSS":
        return not reject_explicit_cross_join
    if join.args.get("method", "").upper() == "LATERAL" or side == "LATERAL":
        return True
    target = join.this
    if isinstance(target, exp.Lateral):
        return True
    # generate_series(...) and friends: a table function, not a table.
    return isinstance(target, exp.Table) and isinstance(target.this, exp.Func)


def _suggest_fk_path(
    catalog: CatalogSnapshot, left: TableInfo, right: TableInfo
) -> str | None:
    """Name a declared FK between the two tables, if one exists.

    When the model joins on the wrong columns, this points at the right ones
    instead of merely saying the join is unsupported.
    """
    for fk in catalog.fk_edges(left.ref):
        other = fk.target if fk.source == left.ref else fk.source
        if other != right.ref:
            continue
        conditions = " AND ".join(
            f"{src.table}.{src.column} = {tgt.table}.{tgt.column}" for src, tgt in fk.pairs()
        )
        return f"A declared foreign key exists: {conditions}"
    return None


def _fragment(node: exp.Expression, limit: int = 200) -> str:
    try:
        text = node.sql(dialect="postgres")
    except Exception:
        text = str(node)
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["check_joins"]
