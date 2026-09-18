"""Le contrat de sortie commun à tous les domaines de décision.

C'est ce que la décision 0002 unifie **à la place** des moteurs. L'irrigation
décide par une cascade de seuils sur une grandeur physique ; la logistique
classe des candidats hétérogènes par critères pondérés. Les deux formes sont
irréductibles l'une à l'autre — mais ce qu'elles *produisent* a la même forme,
et c'est cela qui traverse la persistance, les deux panneaux d'explication et la
question analytique « combien de recommandations ont été acceptées ».

Rien ici ne calcule. Ce module définit la forme d'une décision : ce qui a été
retenu, ce qui a été écarté et pourquoi, ce que cela coûte, sur quoi cela
repose, et ce que l'humain en a fait.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.domain.enums import DataOrigin, DataState, ReliabilityLevel
from app.domain.provenance import DataSourceRef

__all__ = [
    "AlternativeConsidered",
    "Decision",
    "DecisionDomain",
    "DecisionInput",
    "EvidenceItem",
    "HumanVerdict",
    "RejectionReason",
]


class DecisionDomain(StrEnum):
    IRRIGATION = "IRRIGATION"
    LOGISTICS = "LOGISTICS"
    SOURCING = "SOURCING"
    INVENTORY = "INVENTORY"

    @property
    def label_fr(self) -> str:
        return {
            DecisionDomain.IRRIGATION: "Irrigation",
            DecisionDomain.LOGISTICS: "Logistique",
            DecisionDomain.SOURCING: "Approvisionnement",
            DecisionDomain.INVENTORY: "Stocks",
        }[self]


class HumanVerdict(StrEnum):
    """Ce que l'humain a fait de la proposition.

    `PENDING` est l'état initial et il est important : aucun outil n'exécute
    d'action irréversible, et `create_recommendation` enregistre une proposition
    **en attente**, jamais une exécution.
    """

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    MODIFIED = "MODIFIED"

    @property
    def label_fr(self) -> str:
        return {
            HumanVerdict.PENDING: "En attente de décision",
            HumanVerdict.ACCEPTED: "Acceptée",
            HumanVerdict.REJECTED: "Refusée",
            HumanVerdict.MODIFIED: "Appliquée avec modification",
        }[self]


@dataclass(frozen=True, slots=True)
class RejectionReason:
    """Pourquoi une option a été écartée.

    Structurée, et non une phrase libre : l'utilisateur doit pouvoir constater
    qu'une piste évidente a bien été examinée puis rejetée, et le motif doit
    rester filtrable et dénombrable des mois plus tard.
    """

    code: str
    message_fr: str
    #: La grandeur qui a bloqué, quand il y en a une : « capacité 120 t < 180 t
    #: demandées » est contestable, « infaisable » ne l'est pas.
    observed: float | None = None
    limit: float | None = None
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class DecisionInput:
    """Une entrée du calcul, avec sa provenance.

    C'est ce que le panneau « Pourquoi cette décision ? » affiche en tête. Le
    couple `(state, origin)` n'est pas décoratif : c'est ce qui distingue une
    humidité relevée à la sonde d'une humidité issue d'un jeu de démonstration,
    alors que les deux sont « 27 % » dans la même colonne.
    """

    key: str
    label_fr: str
    value: float | str | None
    unit: str | None
    state: DataState
    origin: DataOrigin
    source: DataSourceRef | None = None
    #: Renseigné quand la valeur manque : le panneau affiche alors la raison à
    #: la place du chiffre, plutôt qu'une case vide qui ressemble à un bug.
    missing_reason_fr: str | None = None

    @property
    def is_available(self) -> bool:
        return self.value is not None


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """Un élément du panneau « Sources et preuves ».

    Langage métier en surface, détail technique derrière un dépliant. Jamais un
    journal brut : un responsable d'exploitation ne doit pas avoir à lire une
    trace pour savoir sur quoi repose une recommandation.
    """

    label_fr: str
    detail_fr: str
    state: DataState
    source: DataSourceRef | None = None
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AlternativeConsidered:
    """Une option examinée — retenue, classée, ou écartée avec son motif.

    Les options écartées sont conservées **avec** leur motif. Une recommandation
    qui ne montre que l'option gagnante ressemble à un oracle ; une qui montre
    ce qu'elle a écarté et pourquoi se laisse contester, ce qui est la seule
    façon d'être crue.
    """

    id: str
    label_fr: str
    description_fr: str
    is_feasible: bool
    rejection_reasons: tuple[RejectionReason, ...] = ()
    #: Rang parmi les options faisables. `None` pour une option écartée : classer
    #: une option infaisable lui donnerait une place dans un tableau où elle
    #: n'en a pas.
    rank: int | None = None
    is_recommended: bool = False
    #: Écarts au plan actuel, dans les unités du domaine. Laissés libres parce
    #: qu'ils diffèrent : m³ et MAD ici, heures et points de risque là.
    deltas_fr: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Decision:
    """Une décision, quel que soit le domaine qui l'a produite.

    Persistée telle quelle : une décision doit rester auditable des mois plus
    tard, **après** que la météo et les paramètres de culture ont changé. C'est
    aussi le jeu de données que l'agent d'analyse peut interroger, et la seule
    base possible d'une quantification ultérieure des pertes évitées.
    """

    domain: DecisionDomain
    subject_id: str
    subject_label_fr: str
    #: La conclusion, en une phrase, en français.
    headline_fr: str
    #: Le code stable de la décision (`IRRIGATE`, `DEPARTURE_SHIFT`…), anglais
    #: comme toute valeur stockée.
    outcome_code: str

    inputs: tuple[DecisionInput, ...] = ()
    #: Étapes numérotées, avec le numéro d'équation FAO quand il y en a un.
    calculation_steps_fr: tuple[str, ...] = ()
    assumptions_fr: tuple[str, ...] = ()
    alternatives: tuple[AlternativeConsidered, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    tradeoffs_fr: tuple[str, ...] = ()
    warnings_fr: tuple[str, ...] = ()

    reliability: ReliabilityLevel | None = None
    #: Sorties déclarées indisponibles, avec leur raison. Un rendement sans Ky
    #: documenté vit ici — visible, expliqué, et surtout pas estimé.
    unavailable_fr: dict[str, str] = field(default_factory=dict)

    verdict: HumanVerdict = HumanVerdict.PENDING
    decided_at: datetime | None = None
    decided_by: str | None = None

    @property
    def recommended_alternative(self) -> AlternativeConsidered | None:
        return next((a for a in self.alternatives if a.is_recommended), None)

    @property
    def rejected_alternatives(self) -> tuple[AlternativeConsidered, ...]:
        return tuple(a for a in self.alternatives if not a.is_feasible)

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain.value,
            "domain_label_fr": self.domain.label_fr,
            "subject_id": self.subject_id,
            "subject_label_fr": self.subject_label_fr,
            "headline_fr": self.headline_fr,
            "outcome_code": self.outcome_code,
            "inputs": [
                {
                    "key": i.key,
                    "label_fr": i.label_fr,
                    "value": i.value,
                    "unit": i.unit,
                    "state": i.state.value,
                    "state_label_fr": i.state.label_fr,
                    "origin": i.origin.value,
                    "origin_label_fr": i.origin.label_fr,
                    "source_label_fr": i.source.label_fr if i.source else None,
                    "missing_reason_fr": i.missing_reason_fr,
                }
                for i in self.inputs
            ],
            "calculation_steps_fr": list(self.calculation_steps_fr),
            "assumptions_fr": list(self.assumptions_fr),
            "alternatives": [
                {
                    "id": a.id,
                    "label_fr": a.label_fr,
                    "description_fr": a.description_fr,
                    "is_feasible": a.is_feasible,
                    "rank": a.rank,
                    "is_recommended": a.is_recommended,
                    "deltas_fr": a.deltas_fr,
                    "rejection_reasons": [
                        {
                            "code": r.code,
                            "message_fr": r.message_fr,
                            "observed": r.observed,
                            "limit": r.limit,
                            "unit": r.unit,
                        }
                        for r in a.rejection_reasons
                    ],
                }
                for a in self.alternatives
            ],
            "evidence": [
                {
                    "label_fr": e.label_fr,
                    "detail_fr": e.detail_fr,
                    "state": e.state.value,
                    "state_label_fr": e.state.label_fr,
                    "source_label_fr": e.source.label_fr if e.source else None,
                    "observed_at": e.observed_at.isoformat() if e.observed_at else None,
                }
                for e in self.evidence
            ],
            "tradeoffs_fr": list(self.tradeoffs_fr),
            "warnings_fr": list(self.warnings_fr),
            "reliability": self.reliability.value if self.reliability else None,
            "reliability_label_fr": (
                self.reliability.label_fr if self.reliability else None
            ),
            "unavailable_fr": self.unavailable_fr,
            "verdict": self.verdict.value,
            "verdict_label_fr": self.verdict.label_fr,
        }
