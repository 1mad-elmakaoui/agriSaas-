"""Traçabilité des valeurs — le couple qui voyage de la colonne au pixel.

Trois objets, trois questions distinctes. Les confondre est le défaut que ce
module existe pour empêcher :

* :class:`DataOrigin` — *catégorie* de provenance. Porte le poids de fiabilité
  et la contrainte de schéma. Six valeurs, fermées.
* :class:`DataSourceRef` — *source nommée* (« Open-Meteo, archive ERA5-Land »).
  Ouverte, destinée au panneau de preuves. Ce n'est pas une catégorie : deux
  services externes différents partagent `EXTERNAL_API` et ne partagent pas
  leur référence.
* :class:`Measure` — une valeur, son unité, son état, son origine et sa source.

Règle de sécurité correspondante, appliquée par le schéma de l'API et non par
convention : **aucun point d'entrée HTTP n'accepte `DataState` ni `DataOrigin`
de l'appelant.** Un client ne peut pas déclarer qu'une saisie manuelle vient
d'un capteur. Le serveur décide.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import DataOrigin, DataState, ReliabilityLevel

__all__ = [
    "SOURCE_MANUAL_ENTRY",
    "SOURCE_REFERENCE_FAO",
    "SOURCE_SEED_DEMO",
    "DataSourceRef",
    "Measure",
    "reliability_level",
    "reliability_score",
]


@dataclass(frozen=True, slots=True)
class DataSourceRef:
    """Référence à une source nommée, affichable telle quelle."""

    id: str
    label_fr: str
    kind: str
    detail: str | None = None


SOURCE_MANUAL_ENTRY = DataSourceRef(
    id="manual-entry", label_fr="Saisie de l'exploitant", kind="internal"
)
SOURCE_REFERENCE_FAO = DataSourceRef(
    id="fao-reference",
    label_fr="Référentiel FAO",
    kind="reference",
    detail="FAO-56 et FAO-33, table citée par ligne",
)
SOURCE_SEED_DEMO = DataSourceRef(
    id="seed-demo",
    label_fr="Jeu de démonstration",
    kind="internal",
    detail="Données fabriquées pour la démonstration — jamais une mesure",
)


@dataclass(frozen=True, slots=True)
class Measure:
    """Une valeur numérique inséparable de sa provenance.

    L'unité fait partie du sens : une exposition en millimètres et une
    exposition en heures ne se comparent pas, et un champ nu les laisserait
    se comparer.
    """

    value: float
    unit: str
    state: DataState
    origin: DataOrigin
    source: DataSourceRef
    observed_at: datetime | None = None
    valid_at: datetime | None = None

    def __post_init__(self) -> None:
        # Un état et une origine incompatibles produiraient une puce de source
        # qui ment. Le cas le plus dangereux est « observé » sur une valeur que
        # rien n'a mesurée.
        if self.state.is_measurement and self.origin in _NON_MEASURING_ORIGINS:
            raise ValueError(
                f"État « {self.state.label_fr} » incompatible avec l'origine "
                f"« {self.origin.label_fr} » : rien n'a été mesuré. "
                "Utiliser DERIVED, INFERRED ou SIMULATED."
            )

    @property
    def is_usable_as_fact(self) -> bool:
        """Vrai si la valeur repose sur une mesure, une prévision ou un calcul."""
        return self.state in (DataState.OBSERVED, DataState.FORECAST, DataState.DERIVED)


#: Origines qui, par construction, ne mesurent rien.
_NON_MEASURING_ORIGINS: frozenset[DataOrigin] = frozenset(
    {DataOrigin.REFERENCE_TABLE, DataOrigin.MODEL, DataOrigin.SEED_DEMO}
)


def reliability_score(origins: list[DataOrigin]) -> float | None:
    """Moyenne des poids de fiabilité des entrées.

    Renvoie ``None`` sur une liste vide plutôt que 0.0 : l'absence d'entrée
    n'est pas une fiabilité nulle, c'est une fiabilité inconnue, et la
    distinction décide de ce que l'interface affiche.
    """
    if not origins:
        return None
    return sum(o.reliability_weight for o in origins) / len(origins)


#: Bornes de classement du score. Choix de présentation, pas mesure : elles ne
#: correspondent à aucune validation empirique et sont réglables.
HIGH_RELIABILITY_FLOOR = 0.80
MEDIUM_RELIABILITY_FLOOR = 0.55


def reliability_level(score: float | None) -> ReliabilityLevel | None:
    if score is None:
        return None
    if score >= HIGH_RELIABILITY_FLOOR:
        return ReliabilityLevel.HIGH
    if score >= MEDIUM_RELIABILITY_FLOOR:
        return ReliabilityLevel.MEDIUM
    return ReliabilityLevel.LOW
