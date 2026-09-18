"""Name resolution against the live catalog.

This is where hallucinated tables and columns are caught. Prompt instructions
are a hint; this layer is the guarantee.

Scope analysis comes from ``sqlglot.optimizer.scope.build_scope`` rather than a
hand-rolled walk: it already understands CTEs, derived tables, lateral joins,
and correlated subqueries, and getting any one of those wrong produces false
rejections of valid SQL -- the failure mode that damages trust fastest.

``EXPLAIN`` will also catch a bad name, authoritatively. What this layer adds
is the *message*: PostgreSQL says ``column "custmer_id" does not exist``; this
says which table it resolved against and names the three closest real columns.
That difference is worth several repair attempts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rapidfuzz import process as fuzzy_process
from sqlglot import exp
from sqlglot.optimizer.scope import Scope, build_scope

from app.analytics.catalog import CatalogSnapshot, ColumnRef, TableInfo, TableRef
from app.analytics.validation.compat import as_expression
from app.analytics.validation.models import Severity, ValidationCode, ValidationIssue

_SUGGESTION_COUNT = 3
_SUGGESTION_CUTOFF = 60.0


@dataclass(slots=True)
class ResolvedColumn:
    """A column reference successfully bound to a real catalog column."""

    node: exp.Column
    ref: ColumnRef
    table: TableInfo


@dataclass(slots=True)
class _Sources:
    """Table sources visible at one scope level."""

    physical: dict[str, TableInfo]
    derived: dict[str, frozenset[str]]

    def aliases(self) -> tuple[str, ...]:
        return tuple(sorted({*self.physical, *self.derived}))

    def knows_alias(self, alias: str) -> bool:
        return alias in self.physical or alias in self.derived

    def has_column(self, name: str) -> bool:
        if any(info.column(name) is not None for info in self.physical.values()):
            return True
        return any(name.lower() in columns for columns in self.derived.values())


@dataclass(slots=True)
class Resolution:
    issues: list[ValidationIssue] = field(default_factory=list)
    referenced_tables: list[TableRef] = field(default_factory=list)
    resolved_columns: list[ResolvedColumn] = field(default_factory=list)
    #: Column node -> owning table, for the join and aggregation stages, which
    #: must not redo this work (and must not disagree with it).
    column_owner: dict[int, TableInfo] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(i.is_error for i in self.issues)


class NameResolver:
    def __init__(self, catalog: CatalogSnapshot) -> None:
        self._catalog = catalog

    def resolve(self, root: exp.Expression) -> Resolution:
        result = Resolution()
        root_scope = build_scope(root)
        if root_scope is None:
            return result

        # Two passes. ``Scope.traverse()`` yields children before parents, so
        # every scope's sources must be built before any column is resolved --
        # otherwise a correlated subquery is processed while its enclosing
        # query's aliases are still unknown, and legal SQL is rejected.
        scopes = list(root_scope.traverse())
        sources_by_scope: dict[int, _Sources] = {
            id(scope): self._scope_sources(scope, result) for scope in scopes
        }

        for scope in scopes:
            self._resolve_scope(
                sources_by_scope[id(scope)],
                _merge_ancestors(scope, sources_by_scope),
                scope,
                result,
            )

        # Stable, de-duplicated order so the response and prompts are
        # reproducible run to run.
        seen: dict[TableRef, None] = {}
        for ref in result.referenced_tables:
            seen.setdefault(ref, None)
        result.referenced_tables = list(seen)
        return result

    # -- per-scope ---------------------------------------------------------

    def _scope_sources(self, scope: Scope, result: Resolution) -> _Sources:
        physical: dict[str, TableInfo] = {}
        derived: dict[str, frozenset[str]] = {}

        for alias, source in scope.sources.items():
            if isinstance(source, exp.Table):
                if isinstance(source.this, exp.Func):
                    # A set-returning table function (generate_series(...) AS
                    # g(n)) is not a catalog object and its output columns are
                    # named by the alias clause. Accept any column against it;
                    # the function policy governs whether the call is allowed
                    # at all.
                    derived[alias.lower()] = _UNKNOWN_COLUMNS
                    continue
                info = self._resolve_table(source, result)
                if info is not None:
                    physical[alias.lower()] = info
            elif isinstance(source, Scope):
                derived[alias.lower()] = self._derived_columns(source)

        return _Sources(physical=physical, derived=derived)

    def _resolve_scope(
        self,
        local: _Sources,
        outer: _Sources | None,
        scope: Scope,
        result: Resolution,
    ) -> None:
        for column in scope.columns:
            self._resolve_column(column, local, outer, result)

    def _resolve_table(self, node: exp.Table, result: Resolution) -> TableInfo | None:
        # A table function such as generate_series(...) is not a catalog
        # object; the function policy governs it instead.
        if isinstance(node.this, exp.Func):
            return None

        name = node.name
        if not name:
            return None

        schema = node.db or None
        qualified = f"{schema}.{name}" if schema else name
        info = self._catalog.resolve_table(qualified)

        if info is None:
            result.issues.append(
                ValidationIssue(
                    code=ValidationCode.UNKNOWN_TABLE,
                    severity=Severity.ERROR,
                    message=f'Table "{qualified}" does not exist in the database.',
                    fragment=node.sql(dialect="postgres"),
                    suggestion=self._suggest_table(name),
                )
            )
            return None

        if info.is_foreign:
            # Reading a foreign table reaches the network from the database
            # host, which is outside the boundary this system can reason about.
            result.issues.append(
                ValidationIssue(
                    code=ValidationCode.FOREIGN_TABLE,
                    severity=Severity.ERROR,
                    message=(
                        f'"{info.ref.qualified}" is a foreign table. Querying it performs '
                        "network I/O from the database host and is not permitted."
                    ),
                    fragment=node.sql(dialect="postgres"),
                )
            )
            return None

        result.referenced_tables.append(info.ref)
        return info

    def _resolve_column(
        self,
        column: exp.Column,
        local: _Sources,
        outer: _Sources | None,
        result: Resolution,
    ) -> None:
        if isinstance(column.this, exp.Star):
            return  # handled by the projection stage

        name = column.name
        qualifier = (column.table or "").lower()

        # Inner scopes shadow outer ones, matching PostgreSQL's resolution
        # order; the merged view is only consulted when the local scope has no
        # candidate.
        if qualifier:
            if local.knows_alias(qualifier):
                self._resolve_qualified(column, name, qualifier, local, result)
            elif outer is not None and outer.knows_alias(qualifier):
                self._resolve_qualified(column, name, qualifier, outer, result)
            else:
                self._report_unresolved_alias(column, qualifier, local, outer, result)
            return

        if local.has_column(name) or outer is None or not outer.has_column(name):
            self._resolve_unqualified(column, name, local, result)
        else:
            self._resolve_unqualified(column, name, outer, result)

    def _report_unresolved_alias(
        self,
        column: exp.Column,
        qualifier: str,
        local: _Sources,
        outer: _Sources | None,
        result: Resolution,
    ) -> None:
        known = sorted(set(local.aliases()) | set(outer.aliases() if outer else ()))
        result.issues.append(
            ValidationIssue(
                code=ValidationCode.UNRESOLVED_ALIAS,
                severity=Severity.ERROR,
                message=(
                    f'Table alias "{qualifier}" is not defined in this query. '
                    f"Available: {', '.join(known) if known else 'none'}."
                ),
                fragment=column.sql(dialect="postgres"),
                suggestion=_suggest(qualifier, known),
            )
        )

    def _resolve_qualified(
        self,
        column: exp.Column,
        name: str,
        qualifier: str,
        sources: _Sources,
        result: Resolution,
    ) -> None:
        physical = sources.physical
        derived = sources.derived
        if qualifier in derived:
            if name.lower() not in derived[qualifier]:
                result.issues.append(
                    ValidationIssue(
                        code=ValidationCode.UNKNOWN_COLUMN,
                        severity=Severity.ERROR,
                        message=(
                            f'Column "{name}" is not produced by the subquery or CTE '
                            f'"{qualifier}".'
                        ),
                        fragment=column.sql(dialect="postgres"),
                        suggestion=_suggest(name, sorted(derived[qualifier])),
                    )
                )
            return

        info = physical.get(qualifier)
        if info is None:
            return  # caller already established the alias is unknown

        catalog_column = info.column(name)
        if catalog_column is None:
            result.issues.append(
                ValidationIssue(
                    code=ValidationCode.UNKNOWN_COLUMN,
                    severity=Severity.ERROR,
                    message=(
                        f'Column "{name}" does not exist on table '
                        f'"{info.ref.qualified}".'
                    ),
                    fragment=column.sql(dialect="postgres"),
                    suggestion=_suggest(name, list(info.column_names)),
                )
            )
            return

        result.resolved_columns.append(
            ResolvedColumn(node=column, ref=catalog_column.ref, table=info)
        )
        result.column_owner[id(column)] = info

    def _resolve_unqualified(
        self,
        column: exp.Column,
        name: str,
        sources: _Sources,
        result: Resolution,
    ) -> None:
        physical = sources.physical
        derived = sources.derived
        owners = [info for info in physical.values() if info.column(name) is not None]
        derived_owners = [
            alias for alias, columns in derived.items() if name.lower() in columns
        ]
        total = len(owners) + len(derived_owners)

        if total == 0:
            candidates: list[str] = []
            for info in physical.values():
                candidates.extend(info.column_names)
            for columns in derived.values():
                candidates.extend(columns)
            result.issues.append(
                ValidationIssue(
                    code=ValidationCode.UNKNOWN_COLUMN,
                    severity=Severity.ERROR,
                    message=(
                        f'Column "{name}" does not exist on any table referenced by this '
                        "query."
                    ),
                    fragment=column.sql(dialect="postgres"),
                    suggestion=_suggest(name, candidates),
                )
            )
            return

        if total > 1:
            owner_names = [info.ref.table for info in owners] + derived_owners
            result.issues.append(
                ValidationIssue(
                    code=ValidationCode.AMBIGUOUS_COLUMN,
                    severity=Severity.ERROR,
                    message=(
                        f'Column reference "{name}" is ambiguous: it exists on '
                        f"{', '.join(sorted(owner_names))}."
                    ),
                    fragment=column.sql(dialect="postgres"),
                    suggestion=f"Qualify it, e.g. {sorted(owner_names)[0]}.{name}.",
                )
            )
            return

        if owners:
            info = owners[0]
            catalog_column = info.column(name)
            if catalog_column is not None:
                result.resolved_columns.append(
                    ResolvedColumn(node=column, ref=catalog_column.ref, table=info)
                )
                result.column_owner[id(column)] = info

    # -- helpers -----------------------------------------------------------

    def _derived_columns(self, scope: Scope) -> frozenset[str]:
        """Output column names of a CTE or derived table.

        ``SELECT *`` inside a CTE means the output set is not statically known
        from the CTE alone. Returning an empty set would produce a false
        "column not produced by the CTE" error on perfectly valid SQL, so the
        marker below disables membership checking for that subquery instead --
        ``EXPLAIN`` still catches a genuinely wrong name.
        """
        expression = as_expression(scope.expression)
        if not isinstance(expression, exp.Query):
            # A table function or a VALUES list has no statically known output
            # column set either, and the same marker applies.
            return _UNKNOWN_COLUMNS
        try:
            names = [str(name).lower() for name in expression.named_selects]
        except Exception:
            return _UNKNOWN_COLUMNS
        if any(name == "*" for name in names) or _has_star(expression):
            return _UNKNOWN_COLUMNS
        return frozenset(names)

    def _suggest_table(self, name: str) -> str | None:
        return _suggest(name, [t.ref.table for t in self._catalog.tables])


class _AnyColumnSet(frozenset[str]):
    """Membership set that accepts everything.

    Used where a subquery's output columns cannot be determined statically
    (``SELECT *``). Accepting everything is the right default: a false
    rejection of valid SQL is worse than deferring to EXPLAIN.
    """

    def __contains__(self, item: object) -> bool:
        return True


_UNKNOWN_COLUMNS = _AnyColumnSet()


def _merge_ancestors(
    scope: Scope, sources_by_scope: dict[int, _Sources]
) -> _Sources | None:
    """Sources visible from enclosing scopes, nearest ancestor winning.

    A correlated subquery legally references the outer query's aliases, so
    those have to be visible -- but only after the local scope has had its
    chance, which is why they are kept separate rather than merged in.
    """
    physical: dict[str, TableInfo] = {}
    derived: dict[str, frozenset[str]] = {}
    found = False

    current = scope.parent
    while current is not None:
        ancestor = sources_by_scope.get(id(current))
        if ancestor is not None:
            found = True
            for alias, info in ancestor.physical.items():
                physical.setdefault(alias, info)
            for alias, columns in ancestor.derived.items():
                derived.setdefault(alias, columns)
        current = current.parent

    return _Sources(physical=physical, derived=derived) if found else None


def _has_star(expression: exp.Expression) -> bool:
    if not isinstance(expression, exp.Select):
        return False
    return any(
        isinstance(projection, exp.Star)
        or (isinstance(projection, exp.Column) and isinstance(projection.this, exp.Star))
        for projection in expression.expressions
    )


def _suggest(name: str, candidates: list[str] | tuple[str, ...]) -> str | None:
    """Nearest real names, as a repair hint.

    This is the concrete advantage of validating before execution rather than
    relying on the database error alone.
    """
    pool = sorted({c for c in candidates if c})
    if not pool:
        return None
    matches = fuzzy_process.extract(
        name, pool, limit=_SUGGESTION_COUNT, score_cutoff=_SUGGESTION_CUTOFF
    )
    if not matches:
        return None
    names = [match[0] for match in matches]
    return f"Did you mean: {', '.join(names)}?"


__all__ = ["NameResolver", "Resolution", "ResolvedColumn"]
