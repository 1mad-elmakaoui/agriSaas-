"""Validation pipeline -- layer 1 of the safety stack.

Stages run in a fixed order and the first three short-circuit, because every
later stage assumes a well-formed, single, read-only statement. Resolving
column names inside a ``DELETE`` is wasted work at best and a source of
confusing error messages at worst.

    1. parse                 -- one statement, syntactically valid, bounded
    2. statement class       -- read-only SELECT only, no DML anywhere in the
                                tree, no SELECT INTO, no locking clauses
    3. function policy       -- default-deny (see functions.py)
    3b. relation allowlist   -- analytics views only (see relations.py); the
                                layer the source system did not have
    4. name resolution       -- tables and columns exist; aliases resolve;
                                unqualified references are unambiguous
    5. projection            -- no SELECT *
    6. aggregation           -- aggregate misuse; GROUP BY coverage (warning)
    7. joins                 -- FK-backed / compatible / incompatible
    8. LIMIT injection       -- bound the outermost query

The canonical fingerprint is computed as soon as parsing succeeds and is
attached to *every* report from that point on, including failing ones -- a
query that failed validation is exactly the one a model is about to re-emit.

Everything here is a pure function of ``(sql, CatalogSnapshot, settings)``.
That is deliberate: the highest-value component in the system is fully
unit-testable with a fixture catalog and no database, which is what makes an
adversarial test suite practical.
"""

from __future__ import annotations

import logging

import structlog
from sqlglot import exp, parse
from sqlglot.errors import ParseError, TokenError

from app.analytics.catalog import CatalogSnapshot
from app.analytics.settings import ValidationSettings
from app.analytics.validation.aggregation import check_aggregation
from app.analytics.validation.compat import as_expression
from app.analytics.validation.fingerprint import fingerprint
from app.analytics.validation.functions import FunctionPolicy, function_names
from app.analytics.validation.joins import check_joins
from app.analytics.validation.limits import apply_limit, check_projection
from app.analytics.validation.models import (
    Severity,
    ValidationCode,
    ValidationIssue,
    ValidationReport,
)
from app.analytics.validation.relations import check_relations
from app.analytics.validation.resolver import NameResolver
from app.analytics.validation.statements import check_statement_class

logger = structlog.get_logger(__name__)

DIALECT = "postgres"

# sqlglot warns whenever it falls back to parsing something as `Command`.
# For this system that is the *expected* path for every statement we intend to
# reject (EXPLAIN, DO, CALL, VACUUM), so the warning is noise on the happy path
# of the security layer. The fallback itself is still handled -- Command is in
# FORBIDDEN_NODES and fails closed.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


class SQLValidator:
    """Validates generated SQL against a live catalog snapshot.

    Dialect enforcement is structural, not instructional: the parser is
    constructed for PostgreSQL, so syntax from another dialect fails to parse
    or resolves to something the catalog rejects. Nothing relies on the prompt
    having asked for PostgreSQL.
    """

    def __init__(self, settings: ValidationSettings) -> None:
        self._settings = settings
        self._functions = FunctionPolicy(
            extra_allowed=settings.extra_allowed_functions,
            extra_denied=settings.extra_denied_functions,
        )

    def validate(
        self,
        sql: str,
        catalog: CatalogSnapshot,
        *,
        row_limit: int,
        wants_all_rows: bool = False,
    ) -> ValidationReport:
        # -- stage 1: parse ------------------------------------------------
        parsed = self._parse(sql)
        if isinstance(parsed, ValidationReport):
            return parsed
        root = parsed

        # Fingerprint as soon as there is an AST, not at the end.
        #
        # Computing it only on the success path would mean a query that FAILS
        # validation carries no fingerprint -- and a failing query is precisely
        # the one a model is about to re-emit. De-duplication would then never
        # fire on the case it exists for.
        ast_fingerprint = fingerprint(root)

        # -- stage 2: statement class (security) ---------------------------
        issues: list[ValidationIssue] = list(check_statement_class(root))
        if any(i.is_error for i in issues):
            return ValidationReport(issues=tuple(issues), ast_fingerprint=ast_fingerprint)

        # -- stage 3: function policy (security) ---------------------------
        issues.extend(self._check_functions(root))
        if any(i.is_error and i.is_security for i in issues):
            return ValidationReport(issues=tuple(issues), ast_fingerprint=ast_fingerprint)

        # -- stage 3b: relation allowlist (security) -----------------------
        #
        # Avant la résolution de noms, et non après : une table de base absente
        # du catalogue produirait sinon « table inconnue », c'est-à-dire une
        # faute de frappe là où il y a une tentative de franchissement.
        issues.extend(check_relations(root))
        if any(i.is_error and i.is_security for i in issues):
            return ValidationReport(issues=tuple(issues), ast_fingerprint=ast_fingerprint)

        # -- stage 4: name resolution --------------------------------------
        resolution = NameResolver(catalog).resolve(root)
        issues.extend(resolution.issues)

        # -- stage 5: projection -------------------------------------------
        issues.extend(
            check_projection(root, allow_select_star=self._settings.allow_select_star)
        )

        # -- stages 6 and 7 need resolved columns; skip them when resolution
        # already failed, or they will produce cascading noise that buries the
        # actual error in the repair prompt.
        if resolution.ok:
            issues.extend(check_aggregation(root, resolution.column_owner))
            issues.extend(
                check_joins(
                    root,
                    catalog,
                    resolution.column_owner,
                    strict=self._settings.strict_joins,
                    max_joins=self._settings.max_join_count,
                    reject_explicit_cross_join=(
                        self._settings.reject_explicit_cross_join
                    ),
                )
            )

        if any(i.is_error for i in issues):
            return ValidationReport(
                issues=tuple(issues),
                ast_fingerprint=ast_fingerprint,
                referenced_tables=tuple(resolution.referenced_tables),
            )

        # -- stage 8: LIMIT injection --------------------------------------
        limited = apply_limit(
            root,
            row_limit=row_limit,
            wants_all_rows=wants_all_rows,
            require_limit=self._settings.require_limit,
        )
        issues.extend(limited.issues)

        normalized_sql = limited.expression.sql(dialect=DIALECT, pretty=False)

        # The fingerprint comes from the *original* tree, so injecting a LIMIT
        # does not make an otherwise-identical retry look like a new query.
        return ValidationReport(
            issues=tuple(issues),
            normalized_sql=normalized_sql,
            ast_fingerprint=ast_fingerprint,
            referenced_tables=tuple(resolution.referenced_tables),
            limit_injected=limited.injected,
            effective_limit=limited.effective_limit,
        )

    # -- stage 1 -----------------------------------------------------------

    def _parse(self, sql: str) -> exp.Expression | ValidationReport:
        stripped = sql.strip()
        if not stripped:
            return ValidationReport.failure(
                ValidationCode.EMPTY_STATEMENT, "No SQL statement was produced."
            )

        if len(stripped) > self._settings.max_query_length:
            return ValidationReport.failure(
                ValidationCode.QUERY_TOO_LONG,
                f"Query is {len(stripped)} characters, above the maximum of "
                f"{self._settings.max_query_length}.",
            )

        try:
            statements = parse(stripped, dialect=DIALECT)
        except (ParseError, TokenError) as exc:
            return ValidationReport.failure(
                ValidationCode.PARSE_ERROR,
                f"SQL could not be parsed as PostgreSQL: {exc}",
                fragment=stripped[:200],
            )
        except Exception as exc:
            logger.warning("parser_unexpected_error", error=str(exc))
            return ValidationReport.failure(
                ValidationCode.PARSE_ERROR,
                f"SQL could not be parsed: {type(exc).__name__}: {exc}",
                fragment=stripped[:200],
            )

        parsed = [as_expression(s) for s in statements if s is not None]

        if not parsed:
            return ValidationReport.failure(
                ValidationCode.EMPTY_STATEMENT, "No SQL statement was produced."
            )

        if len(parsed) > 1:
            # Multiple statements are a security matter, not a style one:
            # inspecting only the first would let a second statement through.
            return ValidationReport.failure(
                ValidationCode.MULTIPLE_STATEMENTS,
                f"Expected exactly one statement, found {len(parsed)}. "
                "Only a single read-only query may be executed.",
                fragment="; ".join(s.sql(dialect=DIALECT)[:80] for s in parsed[:3]),
            )

        return parsed[0]

    # -- stage 3 -----------------------------------------------------------

    def _check_functions(self, root: exp.Expression) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        seen: set[str] = set()

        for func in root.find_all(exp.Func):
            node = as_expression(func)
            names = function_names(node)
            if not names:
                continue
            key = "|".join(sorted(n.lower() for n in names))
            if key in seen:
                continue
            seen.add(key)

            permitted, explicitly_denied = self._functions.check(names)
            if permitted:
                continue

            name = sorted(names)[0]

            if explicitly_denied:
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.FORBIDDEN_FUNCTION,
                        severity=Severity.ERROR,
                        message=(
                            f'The function "{name}" is not permitted. It can perform '
                            "I/O, modify state, consume resources, or execute SQL "
                            "outside this query's read-only transaction."
                        ),
                        fragment=_fragment(node),
                    )
                )
            elif isinstance(node, exp.Anonymous):
                # Default-deny. A typed sqlglot node is a standard SQL function
                # by construction; an unrecognised one is not something to
                # assume is safe.
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.UNKNOWN_FUNCTION,
                        severity=Severity.ERROR,
                        message=(
                            f'The function "{name}" is not on the permitted list. Only '
                            "known-safe read-only functions may be used."
                        ),
                        fragment=_fragment(node),
                        suggestion=(
                            "Use a standard SQL function, or ask an operator to add "
                            "this one to validation.extra_allowed_functions."
                        ),
                    )
                )
        return issues


def _fragment(node: exp.Expression, limit: int = 160) -> str:
    try:
        text = node.sql(dialect=DIALECT)
    except Exception:
        text = str(node)
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = ["SQLValidator"]
