"""Suite multilingue : ce qui se mesure hors ligne, et ce qui ne se mesure pas.

Deux mesures distinctes, et la distinction est tout l'intérêt du fichier.

**Mesurable ici, sans clé ni réseau.** Chaque requête de référence passe-t-elle
le validateur contre le catalogue réel ? C'est un contrôle du **corpus**, pas du
modèle : il attrape une référence écrite contre une colonne qui n'existe plus, ce
qui transformerait silencieusement la suite en test de rien. Un corpus dont les
réponses attendues sont invalides mesure la capacité du modèle à reproduire une
erreur.

**Non mesurable ici.** Le modèle produit-il, depuis une question française ou
arabe, une requête équivalente à la référence ? Cela demande une clé d'API. Le
harnais est écrit pour que la mesure soit une commande le jour où il y en a une ;
le registre d'honnêteté dit qu'elle n'a pas été exécutée, et aucun chiffre n'est
inventé à sa place.

La tentation à éviter : rapporter la première mesure sous le nom de la seconde.
« La suite multilingue passe à 100 % » serait vrai de la première et faux de la
seconde, et personne ne relirait la différence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.analytics.catalog import CatalogSnapshot
from app.analytics.settings import ValidationSettings
from app.analytics.validation.pipeline import SQLValidator

__all__ = [
    "MultilingualCase",
    "ReferenceCheck",
    "ReferenceReport",
    "check_references",
    "load_multilingual",
]

CORPUS_PATH = Path(__file__).with_name("multilingual.json")


@dataclass(frozen=True, slots=True)
class MultilingualCase:
    id: str
    language: str
    question: str
    #: `None` pour un cas piège : aucune requête n'est correcte, le comportement
    #: attendu est un refus.
    reference_sql: str | None
    notes: str


@dataclass(frozen=True, slots=True)
class ReferenceCheck:
    case: MultilingualCase
    valid: bool
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReferenceReport:
    checks: tuple[ReferenceCheck, ...]

    @property
    def checked(self) -> int:
        """Les cas pièges ne sont pas comptés : ils n'ont pas de référence."""
        return sum(1 for c in self.checks if c.case.reference_sql is not None)

    @property
    def valid(self) -> int:
        return sum(1 for c in self.checks if c.case.reference_sql is not None and c.valid)

    @property
    def failures(self) -> tuple[ReferenceCheck, ...]:
        return tuple(
            c for c in self.checks if c.case.reference_sql is not None and not c.valid
        )

    def summary_lines_fr(self) -> tuple[str, ...]:
        lines = [
            f"{self.valid}/{self.checked} requêtes de référence valides "
            "contre le catalogue réel."
        ]
        by_language: dict[str, list[ReferenceCheck]] = {}
        for check in self.checks:
            if check.case.reference_sql is not None:
                by_language.setdefault(check.case.language, []).append(check)
        for language, group in sorted(by_language.items()):
            ok = sum(1 for c in group if c.valid)
            lines.append(f"  {language} : {ok}/{len(group)}")
        for failure in self.failures:
            lines.append(f"  INVALIDE {failure.case.id} — {'; '.join(failure.issues)}")
        lines.append(
            "Ceci mesure le corpus, pas le modèle : aucune génération n'a été "
            "exécutée (registre d'honnêteté, ligne 7)."
        )
        return tuple(lines)


def load_multilingual(path: Path = CORPUS_PATH) -> tuple[MultilingualCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return tuple(MultilingualCase(**case) for case in payload["questions"])


def check_references(
    catalog: CatalogSnapshot,
    *,
    settings: ValidationSettings | None = None,
    cases: tuple[MultilingualCase, ...] | None = None,
    row_limit: int = 500,
) -> ReferenceReport:
    validator = SQLValidator(settings or ValidationSettings())
    checks: list[ReferenceCheck] = []

    for case in cases or load_multilingual():
        if case.reference_sql is None:
            checks.append(ReferenceCheck(case=case, valid=False, issues=()))
            continue
        report = validator.validate(case.reference_sql, catalog, row_limit=row_limit)
        errors = tuple(i.message for i in report.issues if i.is_error)
        checks.append(ReferenceCheck(case=case, valid=not errors, issues=errors))

    return ReferenceReport(checks=tuple(checks))
