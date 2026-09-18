"""Classement multicritère — pour les domaines qui ont réellement des candidats.

Trois domaines partagent la forme : logistique, approvisionnement, stocks. Leurs
candidats sont **hétérogènes et incomparables par construction** — un itinéraire
côtier, un fournisseur de Berkane, un transfert entre entrepôts — et ne
deviennent comparables que par normalisation sur un vecteur de critères commun.

L'irrigation n'est pas de ceux-là, et c'est la décision 0002. Sa décision est une
cascade de seuils sur une seule grandeur physique : il n'y a pas de candidats à
générer, il y a une position sur un axe à lire. Lui donner un vecteur de poids
exigerait un modèle d'arbitrage agronomique que la FAO-56 ne fournit pas, et
remplacerait à l'écran une chaîne auditable — « déficit projeté 41,2 mm ≥ seuil
38,0 mm, soit la RFU » — par « report noté 0,62 ». La première est contestable
par un agronome ; la seconde ne l'est par personne.

Trois règles gouvernent ce module :

1. **Les options infaisables ne sont pas classées.** Elles sortent avec leur
   motif. Classer une option qui viole une contrainte dure serait pire
   qu'inutile : ce serait une recommandation dangereuse.
2. **La normalisation est relative au lot comparé.** 45 000 MAD n'est ni bon ni
   mauvais dans l'absolu ; il l'est par rapport aux autres options de cette
   expédition-là.
3. **La pondération dépend du profil, jamais du code.** Elle est affichée :
   savoir que le système a privilégié le risque à 40 % et le coût à 10 % permet
   de contester l'arbitrage, ce qui est sain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.domain.decision import AlternativeConsidered, RejectionReason

__all__ = [
    "CriterionScore",
    "CriterionSpec",
    "OptimizationProfile",
    "RankableCandidate",
    "RankingResult",
    "rank_candidates",
]


@runtime_checkable
class RankableCandidate(Protocol):
    """Ce qu'un candidat doit savoir dire de lui-même pour être classé.

    Un protocole plutôt qu'une classe de base : les trois domaines ont des
    objets métier très différents — une expédition réacheminée n'est pas un
    fournisseur de substitution — et les forcer sous un ancêtre commun
    obligerait à inventer des champs qui ne veulent rien dire chez l'un ou chez
    l'autre. Un `distance_km = 0.0` sur une option de sourcing serait un
    mensonge de schéma dans un produit où chaque valeur porte son état.
    """

    @property
    def id(self) -> str: ...

    @property
    def label_fr(self) -> str: ...

    @property
    def description_fr(self) -> str: ...

    @property
    def is_current_plan(self) -> bool: ...

    def blocking_reasons(self) -> tuple[RejectionReason, ...]:
        """Contraintes dures violées. Vide si l'option est applicable."""
        ...

    def criterion_value(self, key: str) -> float | None:
        """Valeur brute d'un critère, ou `None` si le critère ne s'applique pas."""
        ...


@dataclass(frozen=True, slots=True)
class CriterionSpec:
    """Un critère de comparaison et son sens.

    `higher_is_worse` évite la faute la plus banale du classement multicritère :
    traiter la fiabilité comme le coût. Une fiabilité élevée est bonne, un coût
    élevé ne l'est pas, et une normalisation qui l'ignore range les options
    exactement à l'envers sur ce critère — sans que le score global paraisse
    absurde.
    """

    key: str
    label_fr: str
    unit_fr: str
    higher_is_worse: bool = True


@dataclass(frozen=True, slots=True)
class OptimizationProfile:
    """Pondération par nature de produit.

    Il n'existe pas de pondération universelle : des tomates réfrigérées et du
    blé en vrac n'ont pas la même fonction d'objectif. Ce sont des valeurs par
    défaut, surchargeables par organisation — une donnée de configuration, pas
    une constante du code.
    """

    code: str
    label_fr: str
    rationale_fr: str
    weights: dict[str, float]

    def __post_init__(self) -> None:
        total = sum(self.weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"Les poids du profil « {self.code} » doivent sommer à 1, obtenu "
                f"{total:.4f}. Une somme différente rendrait les scores "
                "incomparables d'un profil à l'autre."
            )


@dataclass(frozen=True, slots=True)
class CriterionScore:
    """Score normalisé d'un critère, **avec** sa valeur brute.

    Garder la valeur brute à côté du score permet d'expliquer « 45 500 MAD, soit
    8 % de plus que le plan actuel » plutôt que « score de coût 0,62 », qui ne
    veut rien dire pour un exploitant.
    """

    key: str
    label_fr: str
    raw_value: float
    unit_fr: str
    normalized: float
    weight: float


@dataclass(frozen=True, slots=True)
class RankingResult:
    profile: OptimizationProfile
    alternatives: tuple[AlternativeConsidered, ...]
    scores: dict[str, tuple[CriterionScore, ...]]
    caveats_fr: tuple[str, ...] = ()

    @property
    def recommended(self) -> AlternativeConsidered | None:
        return next((a for a in self.alternatives if a.is_recommended), None)


def _normalize(value: float, population: list[float], higher_is_worse: bool) -> float:
    """Min-max sur le lot comparé, 0 = meilleur.

    Quand toutes les options partagent la même valeur, l'étendue est nulle :
    renvoyer 0 pour toutes est correct — ce critère ne les départage pas, et
    l'inclure au dénominateur donnerait une division par zéro ou, pire, un
    classement au bruit.
    """
    low, high = min(population), max(population)
    if high == low:
        return 0.0
    ratio = (value - low) / (high - low)
    return round(ratio if higher_is_worse else 1.0 - ratio, 4)


def rank_candidates(
    candidates: list[RankableCandidate],
    *,
    criteria: tuple[CriterionSpec, ...],
    profile: OptimizationProfile,
) -> RankingResult:
    """Filtre puis classe. Jamais l'inverse.

    Les contraintes dures s'appliquent **avant** toute notation. Ranger une
    option infaisable, même en dernier, la met dans un tableau où l'utilisateur
    la lira comme un choix possible.
    """
    feasible: list[RankableCandidate] = []
    rejected: list[AlternativeConsidered] = []

    for candidate in candidates:
        reasons = candidate.blocking_reasons()
        if reasons:
            rejected.append(
                AlternativeConsidered(
                    id=candidate.id,
                    label_fr=candidate.label_fr,
                    description_fr=candidate.description_fr,
                    is_feasible=False,
                    rejection_reasons=reasons,
                )
            )
        else:
            feasible.append(candidate)

    if not feasible:
        return RankingResult(
            profile=profile,
            alternatives=tuple(rejected),
            scores={},
            caveats_fr=(
                "Aucune option ne respecte l'ensemble des contraintes. Une "
                "décision humaine est nécessaire : arbitrer sur l'engagement de "
                "service, la capacité ou le volume expédié.",
            ),
        )

    active = tuple(c for c in criteria if profile.weights.get(c.key, 0.0) > 0)
    scores: dict[str, tuple[CriterionScore, ...]] = {}
    totals: dict[str, float] = {}

    for candidate in feasible:
        entries: list[CriterionScore] = []
        for spec in active:
            value = candidate.criterion_value(spec.key)
            if value is None:
                # Un critère qu'un candidat ne sait pas renseigner n'est pas
                # zéro : il est absent. Le noter à zéro le ferait gagner sur ce
                # critère, ce qui est le pire des deux comportements possibles.
                continue
            population = [
                v
                for v in (c.criterion_value(spec.key) for c in feasible)
                if v is not None
            ]
            entries.append(
                CriterionScore(
                    key=spec.key,
                    label_fr=spec.label_fr,
                    raw_value=round(value, 4),
                    unit_fr=spec.unit_fr,
                    normalized=_normalize(value, population, spec.higher_is_worse),
                    weight=profile.weights[spec.key],
                )
            )
        scores[candidate.id] = tuple(entries)
        # Renormalisé sur les critères réellement renseignés, sinon un candidat
        # auquel il manque un critère coûteux paraîtrait meilleur que les autres.
        weight_sum = sum(e.weight for e in entries)
        totals[candidate.id] = (
            sum(e.normalized * e.weight for e in entries) / weight_sum
            if weight_sum
            else 1.0
        )

    ordered = sorted(feasible, key=lambda c: totals[c.id])
    ranked: list[AlternativeConsidered] = [
        AlternativeConsidered(
            id=candidate.id,
            label_fr=candidate.label_fr,
            description_fr=candidate.description_fr,
            is_feasible=True,
            rank=position,
            is_recommended=position == 1,
        )
        for position, candidate in enumerate(ordered, start=1)
    ]

    return RankingResult(
        profile=profile,
        alternatives=(*ranked, *rejected),
        scores=scores,
        caveats_fr=_caveats(ordered, active),
    )


#: Écart **relatif** en deçà duquel deux valeurs brutes sont tenues pour égales.
#: Choix de présentation, pas mesure : il dit à partir de quand un écart tombe
#: sous la précision des modèles de coût et de durée.
INDISTINGUISHABLE_RELATIVE_GAP = 0.02


def _indistinguishable(
    first: RankableCandidate,
    second: RankableCandidate,
    criteria: tuple[CriterionSpec, ...],
) -> bool:
    """Les deux premières options se départagent-elles réellement ?

    Comparé sur les **valeurs brutes**, jamais sur le score composite — et c'est
    le point délicat de ce module. La normalisation min-max étale toujours le lot
    sur [0, 1] : sur deux candidats, le meilleur vaut 0 et le pire vaut 1, quel
    que soit l'écart réel. Un écart de coût de 0,5 % ressort donc comme un
    avantage maximal.

    C'est précisément la fausse impression de précision que cet avertissement
    existe pour corriger, et la mesurer sur le score l'aurait rendue invisible.
    """
    for spec in criteria:
        a, b = first.criterion_value(spec.key), second.criterion_value(spec.key)
        if a is None or b is None:
            # Un critère qu'une des deux ne renseigne pas ne peut pas prouver
            # qu'elles se ressemblent — mais il ne prouve pas l'inverse non
            # plus. On ne conclut pas sur ce critère-là.
            continue
        scale = max(abs(a), abs(b))
        if scale == 0:
            continue
        if abs(a - b) / scale > INDISTINGUISHABLE_RELATIVE_GAP:
            return False
    return True


def _caveats(
    ordered: list[RankableCandidate], criteria: tuple[CriterionSpec, ...]
) -> tuple[str, ...]:
    caveats: list[str] = []
    if len(ordered) == 1:
        caveats.append(
            "Une seule option respecte les contraintes : il n'y a pas de "
            "véritable arbitrage à opérer."
        )
    elif _indistinguishable(ordered[0], ordered[1], criteria):
        caveats.append(
            "Les deux premières options sont très proches : l'écart est inférieur "
            "à la précision du modèle. Le choix peut légitimement se faire sur un "
            "critère opérationnel non modélisé."
        )
    if not any(c.is_current_plan for c in ordered):
        caveats.append(
            "Le plan actuel ne figure pas parmi les options applicables : les "
            "écarts affichés n'ont donc pas de référence de comparaison."
        )
    return tuple(caveats)
