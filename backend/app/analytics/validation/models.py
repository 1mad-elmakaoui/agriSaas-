"""Validation report model.

Two properties matter here and are enforced by the types rather than by
convention:

* A report is ``ok`` only when it holds no *error*-severity issue. Warnings are
  carried forward and surfaced, never silently dropped, because they are the
  raw material for a good repair prompt when execution later fails.
* ``ast_fingerprint`` is a canonical hash of the parsed statement, not of the
  SQL string. Hashing the string is defeated by whitespace or an alias rename,
  which is exactly what a model does when asked to "try something different".
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.analytics.catalog import TableRef


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ValidationCode(StrEnum):
    """Stable machine-readable codes.

    Used for routing (a forbidden statement routes to reject, not to repair),
    for eval scoring by category, and for building targeted repair prompts.
    """

    # -- structural / parse ------------------------------------------------
    PARSE_ERROR = "parse_error"
    MULTIPLE_STATEMENTS = "multiple_statements"
    QUERY_TOO_LONG = "query_too_long"
    EMPTY_STATEMENT = "empty_statement"

    # -- statement class (security; never repaired, always rejected) -------
    NOT_A_SELECT = "not_a_select"
    FORBIDDEN_STATEMENT = "forbidden_statement"
    CTE_WITH_DML = "cte_with_dml"
    SELECT_INTO = "select_into"
    LOCKING_CLAUSE = "locking_clause"
    FORBIDDEN_FUNCTION = "forbidden_function"
    UNKNOWN_FUNCTION = "unknown_function"

    # -- name resolution ---------------------------------------------------
    UNKNOWN_TABLE = "unknown_table"
    UNKNOWN_COLUMN = "unknown_column"
    AMBIGUOUS_COLUMN = "ambiguous_column"
    UNRESOLVED_ALIAS = "unresolved_alias"
    FOREIGN_TABLE = "foreign_table"
    #: Une relation hors de la surface analytique. Distinct de
    #: `UNKNOWN_TABLE` **délibérément** : `app.shipments` existe, elle est
    #: simplement interdite ici. « Table inconnue » ferait lire une tentative
    #: de franchissement comme une faute de frappe, et l'événement se perdrait
    #: dans le bruit des erreurs de génération.
    RELATION_NOT_ALLOWED = "relation_not_allowed"

    # -- projection & shape ------------------------------------------------
    SELECT_STAR = "select_star"
    MISSING_LIMIT = "missing_limit"
    TOO_MANY_JOINS = "too_many_joins"

    # -- semantics ---------------------------------------------------------
    GROUP_BY_MISMATCH = "group_by_mismatch"
    AGGREGATE_IN_WHERE = "aggregate_in_where"
    NESTED_AGGREGATE = "nested_aggregate"
    NON_FK_JOIN = "non_fk_join"
    TYPE_INCOMPATIBLE_JOIN = "type_incompatible_join"
    CARTESIAN_JOIN = "cartesian_join"


#: Codes that indicate an attempt to modify data or reach outside the read-only
#: boundary. These route to ``reject`` and are never handed to the repair node:
#: if the model emitted a DELETE, asking it to try again is the wrong response.
#: That is a security event to surface, not a syntax error to fix.
SECURITY_CODES: frozenset[ValidationCode] = frozenset(
    {
        ValidationCode.NOT_A_SELECT,
        ValidationCode.FORBIDDEN_STATEMENT,
        ValidationCode.CTE_WITH_DML,
        ValidationCode.SELECT_INTO,
        ValidationCode.LOCKING_CLAUSE,
        ValidationCode.FORBIDDEN_FUNCTION,
        ValidationCode.MULTIPLE_STATEMENTS,
        # Viser une table de base plutôt qu'une vue analytique est une
        # tentative de franchissement, pas une erreur de nommage : on rejette,
        # on ne répare pas.
        ValidationCode.RELATION_NOT_ALLOWED,
    }
)


class ValidationIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ValidationCode
    severity: Severity
    message: str
    #: The offending SQL fragment, when it can be isolated. Giving the repair
    #: model the fragment rather than only a description measurably reduces the
    #: chance it "fixes" an unrelated part of the query.
    fragment: str | None = None
    #: Actionable hint, e.g. the three nearest real column names.
    suggestion: str | None = None

    @property
    def is_error(self) -> bool:
        return self.severity is Severity.ERROR

    @property
    def is_security(self) -> bool:
        return self.code in SECURITY_CODES

    def render(self) -> str:
        parts = [f"[{self.code.value}] {self.message}"]
        if self.fragment:
            parts.append(f"  in: {self.fragment}")
        if self.suggestion:
            parts.append(f"  hint: {self.suggestion}")
        return "\n".join(parts)


class ValidationReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    issues: tuple[ValidationIssue, ...] = ()
    #: Post-normalisation SQL, including any injected LIMIT. This -- not the
    #: model's raw output -- is what gets executed.
    normalized_sql: str | None = None
    ast_fingerprint: str | None = None
    referenced_tables: tuple[TableRef, ...] = ()
    limit_injected: bool = False
    effective_limit: int | None = None

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if i.is_error)

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return tuple(i for i in self.issues if not i.is_error)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def has_security_error(self) -> bool:
        return any(i.is_error and i.is_security for i in self.issues)

    def render_errors(self) -> str:
        """Error text for a repair prompt."""
        return "\n".join(i.render() for i in self.errors)

    def render_all(self) -> str:
        return "\n".join(i.render() for i in self.issues)

    @classmethod
    def failure(
        cls,
        code: ValidationCode,
        message: str,
        *,
        fragment: str | None = None,
        suggestion: str | None = None,
    ) -> ValidationReport:
        """Single-error report -- used by the short-circuiting early stages."""
        return cls(
            issues=(
                ValidationIssue(
                    code=code,
                    severity=Severity.ERROR,
                    message=message,
                    fragment=fragment,
                    suggestion=suggestion,
                ),
            )
        )


class SafetyDecision(StrEnum):
    ALLOW = "allow"
    #: Repairable: the query is legal but too expensive. The model can add
    #: filters or narrow the time range.
    TOO_EXPENSIVE = "too_expensive"
    #: EXPLAIN reported a semantic error the AST layer did not catch --
    #: authoritative, and repairable.
    SEMANTIC_ERROR = "semantic_error"
    #: Never repaired.
    REJECTED = "rejected"


class SafetyReport(BaseModel):
    """Outcome of layer 4 (EXPLAIN + cost threshold)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: SafetyDecision = SafetyDecision.ALLOW
    total_cost: float | None = None
    plan_rows: float | None = None
    threshold: float | None = None
    plan: dict[str, object] | None = None
    message: str | None = None
    skipped: bool = False
    duration_ms: int = Field(default=0, ge=0)

    @property
    def allowed(self) -> bool:
        return self.decision is SafetyDecision.ALLOW


__all__ = [
    "SECURITY_CODES",
    "SafetyDecision",
    "SafetyReport",
    "Severity",
    "ValidationCode",
    "ValidationIssue",
    "ValidationReport",
]
