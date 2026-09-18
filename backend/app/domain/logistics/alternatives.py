"""Génération d'options face à une perturbation.

Ce module **génère et filtre**. Il ne classe pas : le classement appartient à
`app.domain.ranking`, partagé avec les autres domaines (décision 0002). Mélanger
les deux masquerait la distinction qui compte pour un exploitant — *ce qui est
possible* d'un côté, *ce qui est préférable* de l'autre.

Deux notions à ne jamais confondre :

* **Contrainte dure** — une option qui la viole est infaisable et sort du
  classement. Recommander un plan qui ne respecte pas l'engagement de service,
  ou qui dépasse la capacité disponible, serait pire qu'inutile.
* **Préférence** — ce qui distingue deux options toutes deux applicables. C'est
  là, et seulement là, qu'intervient la pondération multicritère.

Les options écartées sont conservées **avec leur motif chiffré**. Un exploitant
doit pouvoir constater qu'une piste évidente a bien été examinée puis rejetée :
« capacité 120 t < 180 t demandées » se conteste, « infaisable » ne se conteste
pas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.decision import RejectionReason
from app.domain.enums import TransportMode
from app.domain.formatting import fr
from app.domain.logistics.exposure import RouteAssessment, WeatherAlongRoute, assess_route
from app.domain.logistics.network import RouteGeometry
from app.domain.provenance import DataSourceRef
from app.domain.ranking import CriterionSpec, OptimizationProfile, RankableCandidate

__all__ = [
    "LOGISTICS_CRITERIA",
    "PERISHABLE_PROFILE",
    "STAPLE_PROFILE",
    "ShipmentContext",
    "TransportAlternative",
    "generate_alternatives",
    "profile_for",
]

#: Décalages de départ examinés, en heures. Les valeurs **négatives** comptent
#: autant que les positives : avancer un départ est souvent la meilleure réponse
#: à un front qui arrive, et un moteur qui n'explorerait que les retards ne la
#: trouverait jamais.
DEPARTURE_SHIFTS_HOURS: tuple[float, ...] = (-10.0, -6.0, -3.0, 3.0, 6.0, 10.0, 14.0)

#: Délai minimal entre maintenant et un départ : préparation, chargement,
#: documents. Sans lui, le moteur recommanderait de partir il y a une heure.
MIN_PREPARATION_HOURS = 3.0

LOGISTICS_CRITERIA: tuple[CriterionSpec, ...] = (
    CriterionSpec("risk", "Exposition au risque", "indice", higher_is_worse=True),
    CriterionSpec("cost_mad", "Coût estimé", "MAD", higher_is_worse=True),
    CriterionSpec("duration_hours", "Durée de trajet", "h", higher_is_worse=True),
    CriterionSpec("sla_margin_hours", "Marge sur l'échéance", "h", higher_is_worse=False),
)

#: Denrée périssable : le risque et la marge d'échéance priment sur le coût. Un
#: chargement de tomates perdu coûte davantage que le détour qui l'aurait sauvé.
PERISHABLE_PROFILE = OptimizationProfile(
    code="PERISHABLE",
    label_fr="Denrée périssable",
    rationale_fr=(
        "Le risque et le respect de l'échéance pèsent plus que le coût : une "
        "marchandise arrivée avariée ou hors délai est perdue en totalité, "
        "tandis qu'un détour ne coûte que son détour."
    ),
    weights={"risk": 0.40, "cost_mad": 0.15, "duration_hours": 0.20, "sla_margin_hours": 0.25},
)

#: Produit stockable : le coût redevient déterminant, le délai se rattrape.
STAPLE_PROFILE = OptimizationProfile(
    code="STAPLE",
    label_fr="Produit stockable",
    rationale_fr=(
        "Un produit non périssable supporte un retard ; le coût redevient donc "
        "le critère dominant."
    ),
    weights={"risk": 0.25, "cost_mad": 0.45, "duration_hours": 0.20, "sla_margin_hours": 0.10},
)


def profile_for(*, requires_cold_chain: bool) -> OptimizationProfile:
    """Profil par nature de produit.

    Il n'existe pas de pondération universelle, et en choisir une par défaut
    « raisonnable » reviendrait à décider à la place de l'exploitant sans le
    dire. Le critère retenu — chaîne du froid — est celui qui sépare le plus
    nettement les deux comportements, et il vient du référentiel, pas d'un
    réglage.
    """
    return PERISHABLE_PROFILE if requires_cold_chain else STAPLE_PROFILE


@dataclass(frozen=True, slots=True)
class ShipmentContext:
    """Ce qu'il faut savoir d'une expédition pour lui chercher des options."""

    reference: str
    product_name_fr: str
    requires_cold_chain: bool
    volume_tonnes: float
    transport_mode: TransportMode
    origin_node_code: str
    origin_name_fr: str
    destination_node_code: str
    destination_name_fr: str
    destination_capacity_tonnes: float | None
    departure_at: datetime
    sla_deadline_at: datetime
    #: Instant de référence pour « trop tôt pour partir ». Passé explicitement
    #: plutôt que lu dans l'horloge : un moteur pur ne consulte pas l'heure, et
    #: un test qui doit attendre demain n'est pas un test.
    now: datetime


@dataclass(frozen=True, slots=True)
class TransportAlternative:
    """Une option évaluée, prête pour le classement.

    Satisfait le protocole `RankableCandidate` : `id`, `label_fr`,
    `description_fr`, `is_current_plan`, `blocking_reasons()`,
    `criterion_value()`.
    """

    id: str
    label_fr: str
    description_fr: str
    kind: str
    is_current_plan: bool
    assessment: RouteAssessment | None
    rejections: tuple[RejectionReason, ...]
    #: Surcoût hors transport : marchandise d'un autre fournisseur, second trajet
    #: après une rupture de charge. L'ignorer fausserait l'arbitrage.
    extra_cost_mad: float = 0.0
    extra_duration_hours: float = 0.0
    tradeoffs_fr: tuple[str, ...] = ()

    def blocking_reasons(self) -> tuple[RejectionReason, ...]:
        return self.rejections

    @property
    def total_cost_mad(self) -> float | None:
        if self.assessment is None:
            return None
        return round(self.assessment.cost.total_mad + self.extra_cost_mad, 0)

    @property
    def total_duration_hours(self) -> float | None:
        if self.assessment is None:
            return None
        return round(self.assessment.adjusted_duration_hours + self.extra_duration_hours, 2)

    @property
    def arrival_at(self) -> datetime | None:
        if self.assessment is None:
            return None
        return self.assessment.estimated_arrival_at + timedelta(hours=self.extra_duration_hours)

    @property
    def sla_margin_hours(self) -> float | None:
        arrival = self.arrival_at
        if arrival is None or self.assessment is None:
            return None
        if self.assessment.sla_deadline_at is None:
            return None
        return round(
            (self.assessment.sla_deadline_at - arrival).total_seconds() / 3600, 2
        )

    def criterion_value(self, key: str) -> float | None:
        if self.assessment is None:
            return None
        if key == "risk":
            return self.assessment.risk_score
        if key == "cost_mad":
            return self.total_cost_mad
        if key == "duration_hours":
            return self.total_duration_hours
        if key == "sla_margin_hours":
            return self.sla_margin_hours
        return None


def generate_alternatives(
    context: ShipmentContext,
    routes: list[RouteGeometry],
    weather: WeatherAlongRoute,
    *,
    sources: tuple[DataSourceRef, ...],
) -> list[TransportAlternative]:
    """Plan actuel, itinéraires de repli, décalages de départ.

    Trois familles seulement. Les options de sourcing et d'entrepôt du moteur
    d'origine ne sont pas portées : elles demandent des fournisseurs et des
    stocks, que ce dépôt n'a pas encore. Les inventer donnerait des alternatives
    plausibles fondées sur rien.
    """
    if not routes:
        return []

    current_route = routes[0]
    options: list[TransportAlternative] = [
        _from_route(
            context,
            route=current_route,
            weather=weather,
            departure_at=context.departure_at,
            identifier="plan-actuel",
            kind="CURRENT_PLAN",
            label_fr="Plan actuel",
            description_fr=(
                f"Itinéraire prévu {context.origin_name_fr} → "
                f"{context.destination_name_fr}, départ inchangé."
            ),
            is_current_plan=True,
            sources=sources,
        )
    ]

    for index, route in enumerate(routes[1:], start=1):
        options.append(
            _from_route(
                context,
                route=route,
                weather=weather,
                departure_at=context.departure_at,
                identifier=f"itineraire-{index}",
                kind="ALTERNATE_ROUTE",
                label_fr=route.label_fr,
                description_fr=(
                    "Corridor différent, départ inchangé "
                    f"({fr(route.total_distance_km, 0)} km)."
                ),
                is_current_plan=False,
                sources=sources,
            )
        )

    options.extend(_departure_shifts(context, current_route, weather, sources=sources))
    return options


# --- interne ---------------------------------------------------------------


def _departure_shifts(
    context: ShipmentContext,
    route: RouteGeometry,
    weather: WeatherAlongRoute,
    *,
    sources: tuple[DataSourceRef, ...],
) -> list[TransportAlternative]:
    options: list[TransportAlternative] = []
    earliest = context.now + timedelta(hours=MIN_PREPARATION_HOURS)

    for shift in DEPARTURE_SHIFTS_HOURS:
        departure = context.departure_at + timedelta(hours=shift)
        advancing = shift < 0
        label = f"{'Avancer' if advancing else 'Décaler'} le départ de {fr(abs(shift), 0)} h"
        identifier = f"depart-{'avance' if advancing else 'retard'}-{abs(shift):.0f}h"

        if departure < earliest:
            options.append(
                TransportAlternative(
                    id=identifier,
                    label_fr=label,
                    description_fr=(
                        f"Départ à {departure:%d/%m %H:%M}, avant le délai de "
                        "préparation minimal."
                    ),
                    kind="DEPARTURE_SHIFT",
                    is_current_plan=False,
                    assessment=None,
                    rejections=(
                        RejectionReason(
                            code="preparation_time",
                            message_fr=(
                                "Départ trop proche : un minimum de "
                                f"{fr(MIN_PREPARATION_HOURS, 0)} h est nécessaire pour "
                                "préparer et charger l'expédition."
                            ),
                            observed=round(
                                (departure - context.now).total_seconds() / 3600, 2
                            ),
                            limit=MIN_PREPARATION_HOURS,
                            unit="h",
                        ),
                    ),
                )
            )
            continue

        options.append(
            _from_route(
                context,
                route=route,
                weather=weather,
                departure_at=departure,
                identifier=identifier,
                kind="DEPARTURE_SHIFT",
                label_fr=label,
                description_fr=(
                    f"Même itinéraire, départ {'avancé' if advancing else 'reporté'} à "
                    f"{departure:%d/%m %H:%M} pour "
                    f"{'passer avant' if advancing else 'laisser passer'} la perturbation."
                ),
                is_current_plan=False,
                sources=sources,
            )
        )
    return options


def _from_route(
    context: ShipmentContext,
    *,
    route: RouteGeometry,
    weather: WeatherAlongRoute,
    departure_at: datetime,
    identifier: str,
    kind: str,
    label_fr: str,
    description_fr: str,
    is_current_plan: bool,
    sources: tuple[DataSourceRef, ...],
    extra_cost_mad: float = 0.0,
    extra_duration_hours: float = 0.0,
    tradeoffs_fr: tuple[str, ...] = (),
) -> TransportAlternative:
    assessment = assess_route(
        route,
        weather,
        departure_at=departure_at,
        volume_tonnes=context.volume_tonnes,
        transport_mode=context.transport_mode,
        sla_deadline_at=context.sla_deadline_at,
        sources=sources,
    )

    arrival = assessment.estimated_arrival_at + timedelta(hours=extra_duration_hours)
    margin = round((context.sla_deadline_at - arrival).total_seconds() / 3600, 2)

    rejections: list[RejectionReason] = []
    if margin < 0:
        rejections.append(
            RejectionReason(
                code="sla_missed",
                message_fr=(
                    f"Arrivée estimée le {arrival:%d/%m à %H:%M}, soit "
                    f"{fr(abs(margin), 1)} h après l'échéance de service."
                ),
                observed=round(abs(margin), 2),
                limit=0.0,
                unit="h",
            )
        )
    if (
        context.destination_capacity_tonnes is not None
        and context.destination_capacity_tonnes < context.volume_tonnes
    ):
        rejections.append(
            RejectionReason(
                code="destination_capacity",
                message_fr=(
                    f"Capacité de destination {fr(context.destination_capacity_tonnes, 0)} t "
                    f"insuffisante pour {fr(context.volume_tonnes, 0)} t."
                ),
                observed=context.volume_tonnes,
                limit=context.destination_capacity_tonnes,
                unit="t",
            )
        )

    return TransportAlternative(
        id=identifier,
        label_fr=label_fr,
        description_fr=description_fr,
        kind=kind,
        is_current_plan=is_current_plan,
        assessment=assessment,
        rejections=tuple(rejections),
        extra_cost_mad=extra_cost_mad,
        extra_duration_hours=extra_duration_hours,
        tradeoffs_fr=tradeoffs_fr,
    )


def _protocol_check(candidate: TransportAlternative) -> RankableCandidate:
    """Vérifié par `mypy` : `TransportAlternative` honore le protocole de classement.

    Sans cette ligne, une divergence entre le protocole et la classe ne se
    verrait qu'à l'exécution — sous la forme d'un tableau comparatif vide, ce
    qui est le symptôme le plus difficile à relier à sa cause.
    """
    return candidate
