"""Liste blanche de relations — la couche que la source d'origine n'a pas.

Le système d'origine listait la multi-location comme un non-objectif. Ses
quatre couches ne l'adressent donc pas, et un `SELECT * FROM shipments`
parfaitement valide, peu coûteux et correct renvoie les expéditions de **toutes**
les organisations.

Ici, la surface analytique est faite de vues, et de vues seulement. Ce module
refuse toute relation qui n'en est pas une.

Deux raisons de le faire explicitement plutôt que de s'en remettre à la
résolution de noms — qui rejetterait déjà `app.shipments`, absente du catalogue :

1. **Le message n'est pas le même.** « Table inconnue » ferait lire une tentative
   de franchissement comme une faute de frappe. Le code `RELATION_NOT_ALLOWED`
   la classe comme un événement de sécurité, jamais réparé, toujours signalé.
2. **La garantie devient lisible.** Un auditeur cherche « où est appliquée la
   liste blanche » et trouve un fichier, pas une propriété émergente du fait que
   le catalogue a été peuplé d'une certaine façon. Une garantie qu'il faut
   déduire est une garantie que le prochain changement casse.

La liste blanche n'est **pas** la couche de sécurité principale : la RLS l'est,
et elle tient même si ce fichier est faux. Celle-ci existe pour que l'erreur soit
comprise, et pour que la tentative soit visible.
"""

from __future__ import annotations

from sqlglot import exp

from app.analytics.validation.models import (
    Severity,
    ValidationCode,
    ValidationIssue,
)

__all__ = ["ANALYTICS_SCHEMA", "check_relations"]

#: Le seul schéma qu'une requête analytique peut nommer.
ANALYTICS_SCHEMA = "analytics"


def check_relations(
    root: exp.Expression, *, allowed_schema: str = ANALYTICS_SCHEMA
) -> list[ValidationIssue]:
    """Refuse toute table qui n'est pas une vue du schéma analytique.

    Les alias de CTE ne sont pas des relations physiques : une requête qui
    déclare `WITH recent AS (...) SELECT ... FROM recent` nomme `recent`, qui
    n'existe dans aucun schéma. Les collecter d'abord évite de rejeter une
    requête parfaitement légitime.
    """
    cte_names = {
        cte.alias_or_name.lower()
        for cte in root.find_all(exp.CTE)
        if cte.alias_or_name
    }

    issues: list[ValidationIssue] = []
    seen: set[str] = set()

    for table in root.find_all(exp.Table):
        name = (table.name or "").lower()
        if not name or name in cte_names:
            continue
        schema = (table.db or "").lower()
        if schema == allowed_schema.lower():
            continue

        qualified = f"{schema}.{name}" if schema else name
        if qualified in seen:
            continue
        seen.add(qualified)

        issues.append(
            ValidationIssue(
                code=ValidationCode.RELATION_NOT_ALLOWED,
                severity=Severity.ERROR,
                message=(
                    f"La relation « {qualified} » est hors de la surface analytique. "
                    f"Seules les vues du schéma « {allowed_schema} » sont "
                    "interrogeables."
                ),
                fragment=qualified,
                suggestion=(
                    "Les tables de base ne sont jamais accessibles depuis cette "
                    "voie : elles contiennent les données de toutes les "
                    "organisations, et seules les vues portent le filtre."
                ),
            )
        )

    return issues
