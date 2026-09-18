"""Harnais du corpus adverse.

Il mesure deux choses, et la seconde est celle qui compte :

1. **Le taux d'attrape.** Combien de cas la couche 1 refuse.
2. **La couche qui a attrapé.** Un cas prévu pour la liste blanche de relations
   mais refusé par la résolution de noms *passe* au sens du premier chiffre, et
   pourtant la garantie annoncée ne tient pas : elle tiendrait par accident,
   jusqu'au jour où le catalogue changerait. Le harnais compte donc les deux.

Aucun modèle, aucun réseau, aucune base : le corpus s'exécute contre un
instantané de catalogue en mémoire. C'est ce qui rend la mesure reproductible et
exécutable en intégration continue, et c'est pourquoi la couche 1 est
délibérément une fonction pure de `(sql, catalogue, réglages)`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.analytics.catalog import CatalogSnapshot
from app.analytics.settings import ValidationSettings
from app.analytics.validation.models import (
    SECURITY_CODES,
    ValidationCode,
    ValidationReport,
)
from app.analytics.validation.pipeline import SQLValidator

__all__ = ["AdversarialCase", "CaseOutcome", "CorpusReport", "load_corpus", "run_corpus"]

CORPUS_PATH = Path(__file__).with_name("adversarial.json")

#: Code de validation → couche qui l'a produit. Sert à vérifier qu'un cas est
#: attrapé **par la couche annoncée**, pas seulement attrapé.
_CODE_TO_LAYER: dict[ValidationCode, str] = {
    ValidationCode.PARSE_ERROR: "parse",
    ValidationCode.EMPTY_STATEMENT: "parse",
    ValidationCode.QUERY_TOO_LONG: "parse",
    ValidationCode.MULTIPLE_STATEMENTS: "statement_class",
    ValidationCode.NOT_A_SELECT: "statement_class",
    ValidationCode.FORBIDDEN_STATEMENT: "statement_class",
    ValidationCode.CTE_WITH_DML: "statement_class",
    ValidationCode.SELECT_INTO: "statement_class",
    ValidationCode.LOCKING_CLAUSE: "statement_class",
    ValidationCode.FORBIDDEN_FUNCTION: "function_policy",
    ValidationCode.UNKNOWN_FUNCTION: "function_policy",
    ValidationCode.RELATION_NOT_ALLOWED: "relation_allowlist",
    ValidationCode.UNKNOWN_TABLE: "name_resolution",
    ValidationCode.UNKNOWN_COLUMN: "name_resolution",
    ValidationCode.AMBIGUOUS_COLUMN: "name_resolution",
    ValidationCode.UNRESOLVED_ALIAS: "name_resolution",
    ValidationCode.FOREIGN_TABLE: "name_resolution",
    ValidationCode.SELECT_STAR: "projection",
    ValidationCode.MISSING_LIMIT: "projection",
    ValidationCode.TOO_MANY_JOINS: "joins",
    ValidationCode.NON_FK_JOIN: "joins",
    ValidationCode.TYPE_INCOMPATIBLE_JOIN: "joins",
    ValidationCode.CARTESIAN_JOIN: "joins",
    ValidationCode.GROUP_BY_MISMATCH: "aggregation",
    ValidationCode.AGGREGATE_IN_WHERE: "aggregation",
    ValidationCode.NESTED_AGGREGATE: "aggregation",
}


@dataclass(frozen=True, slots=True)
class AdversarialCase:
    id: str
    sql: str
    #: `security`, `validation`, ou `explain` — ce dernier n'est **pas**
    #: attendu à la couche 1, et le compter comme un échec fausserait la mesure.
    expect: str
    why: str
    layer: str


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    case: AdversarialCase
    caught: bool
    #: Vrai quand le refus est classé comme sécurité — donc jamais réparé.
    classified_security: bool
    caught_by_layer: str | None
    codes: tuple[str, ...]

    @property
    def layer_as_declared(self) -> bool:
        return self.caught_by_layer == self.case.layer

    @property
    def passed(self) -> bool:
        """Ce que « réussi » veut dire, par catégorie.

        Un cas `explain` est **attendu comme non attrapé** à la couche 1 : c'est
        la démonstration honnête de ce que l'analyse statique ne peut pas faire.
        Le compter comme un échec gonflerait artificiellement l'envie de le
        rattraper au mauvais endroit.
        """
        if self.case.expect == "explain":
            return not self.caught
        if self.case.expect == "security":
            return self.caught and self.classified_security and self.layer_as_declared
        return self.caught and self.layer_as_declared


@dataclass(frozen=True, slots=True)
class CorpusReport:
    outcomes: tuple[CaseOutcome, ...]

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def passed(self) -> int:
        return sum(1 for o in self.outcomes if o.passed)

    @property
    def failures(self) -> tuple[CaseOutcome, ...]:
        return tuple(o for o in self.outcomes if not o.passed)

    def by_category(self, category: str) -> tuple[CaseOutcome, ...]:
        return tuple(o for o in self.outcomes if o.case.expect == category)

    def summary_lines_fr(self) -> tuple[str, ...]:
        lines = [f"{self.passed}/{self.total} cas conformes."]
        for category in ("security", "validation", "explain"):
            subset = self.by_category(category)
            if subset:
                ok = sum(1 for o in subset if o.passed)
                lines.append(f"  {category:11} {ok}/{len(subset)}")
        for outcome in self.failures:
            lines.append(
                f"  ÉCHEC {outcome.case.id} — attendu à « {outcome.case.layer} », "
                f"attrapé par « {outcome.caught_by_layer or 'aucune couche'} » "
                f"({', '.join(outcome.codes) or 'aucun code'})"
            )
        return tuple(lines)


def load_corpus(path: Path = CORPUS_PATH) -> tuple[AdversarialCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(AdversarialCase(**case) for case in payload["cases"])


def run_corpus(
    catalog: CatalogSnapshot,
    *,
    settings: ValidationSettings | None = None,
    cases: tuple[AdversarialCase, ...] | None = None,
    row_limit: int = 500,
) -> CorpusReport:
    validator = SQLValidator(settings or ValidationSettings())
    outcomes: list[CaseOutcome] = []

    for case in cases or load_corpus():
        report = validator.validate(case.sql, catalog, row_limit=row_limit)
        outcomes.append(_outcome(case, report))

    return CorpusReport(outcomes=tuple(outcomes))


def _outcome(case: AdversarialCase, report: ValidationReport) -> CaseOutcome:
    errors = [issue for issue in report.issues if issue.is_error]
    codes = tuple(issue.code.value for issue in errors)
    layer: str | None = None
    for issue in errors:
        mapped = _CODE_TO_LAYER.get(issue.code)
        if mapped is not None:
            layer = mapped
            break
    return CaseOutcome(
        case=case,
        caught=bool(errors),
        classified_security=any(issue.code in SECURITY_CODES for issue in errors),
        caught_by_layer=layer,
        codes=codes,
    )
