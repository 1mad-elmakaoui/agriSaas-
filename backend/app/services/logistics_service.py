"""Analyse de risque d'une expédition, et alternatives.

Ce service **orchestre** : il résout l'expédition et ses sites depuis la base,
rattache origine et destination au graphe routier, échantillonne la météo le long
du tracé, appelle les moteurs purs, classe le résultat avec le moteur de
classement partagé, et met le tout en forme dans le contrat de décision commun.

Il ne calcule rien lui-même. Tout chiffre qui sort d'ici vient d'un module de
`domain/`, et c'est ce qui permet à l'écran, à l'outil du copilote et au serveur
MCP de montrer le même nombre.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.weather import HourlyWeather, WeatherProvider
from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.base import Crop, Product, Shipment, Site
from app.domain.decision import (
    Decision,
    DecisionDomain,
    DecisionInput,
    EvidenceItem,
)
from app.domain.enums import DataOrigin, DataState, RiskLevel, TransportMode
from app.domain.formatting import fr
from app.domain.geo import Coordinates, interpolate
from app.domain.logistics.alternatives import (
    LOGISTICS_CRITERIA,
    ShipmentContext,
    TransportAlternative,
    generate_alternatives,
    profile_for,
)
from app.domain.logistics.exposure import HourlyConditions, WeatherAlongRoute
from app.domain.logistics.morocco import nearest_node
from app.domain.logistics.network import (
    RouteGeometry,
    RoutingError,
    alternative_routes,
)
from app.domain.provenance import DataSourceRef, reliability_level
from app.domain.ranking import RankingResult, rank_candidates

logger = get_logger(__name__)

__all__ = ["LogisticsOutcome", "LogisticsService"]

SOURCE_ROAD_NETWORK = DataSourceRef(
    id="road-network",
    label_fr="Réseau routier de référence",
    kind="reference",
)
SOURCE_RISK_ENGINE = DataSourceRef(
    id="risk-engine", label_fr="Moteur de risque routier", kind="model"
)

#: Horizon d'échantillonnage. Au-delà, une prévision horaire n'apporte plus
#: d'information exploitable pour une décision de départ.
FORECAST_HOURS = 72


@dataclass(frozen=True, slots=True)
class LogisticsOutcome:
    """La décision, plus les objets bruts pour qui veut le détail."""

    decision: Decision
    ranking: RankingResult
    alternatives: tuple[TransportAlternative, ...]
    current_plan: TransportAlternative
    recommended: TransportAlternative | None


class LogisticsService:
    def __init__(self, session: AsyncSession, weather: WeatherProvider) -> None:
        self._session = session
        self._weather = weather

    async def analyse(self, reference: str, *, now: datetime | None = None) -> LogisticsOutcome:
        moment = now or datetime.now(UTC)
        shipment = await self._shipment(reference)
        origin, destination = await self._sites(shipment)
        product_name, requires_cold_chain = await self._product(shipment)

        origin_node = nearest_node(Coordinates(origin.latitude, origin.longitude))
        destination_node = nearest_node(
            Coordinates(destination.latitude, destination.longitude)
        )
        if origin_node.code == destination_node.code:
            # Deux sites rattachés au même nœud : le graphe ne peut pas décrire
            # un trajet entre eux, et prétendre le contraire produirait un
            # itinéraire dégénéré de zéro kilomètre.
            raise ValidationError(
                f"Origine et destination de « {shipment.reference} » sont rattachées au "
                f"même nœud routier ({origin_node.name_fr}) : aucun itinéraire ne peut "
                "être comparé.",
                remedy_fr="Aucune analyse n'est produite pour cette expédition.",
            )

        try:
            routes = alternative_routes(origin_node.code, destination_node.code, max_routes=3)
        except RoutingError as exc:
            raise NotFoundError(str(exc), remedy_fr="Aucune analyse n'est produite.") from exc

        weather = await self._weather_along(routes)
        context = ShipmentContext(
            reference=shipment.reference,
            product_name_fr=product_name or "Produit non renseigné",
            requires_cold_chain=bool(requires_cold_chain),
            volume_tonnes=shipment.volume_tonnes,
            transport_mode=shipment.transport_mode,
            origin_node_code=origin_node.code,
            origin_name_fr=origin.name_fr,
            destination_node_code=destination_node.code,
            destination_name_fr=destination.name_fr,
            destination_capacity_tonnes=destination.capacity_tonnes,
            departure_at=shipment.departure_at,
            sla_deadline_at=shipment.sla_deadline_at,
            now=moment,
        )

        options = generate_alternatives(
            context, routes, weather, sources=(SOURCE_ROAD_NETWORK, SOURCE_RISK_ENGINE)
        )
        if not options:
            raise NotFoundError(
                f"Aucune option n'a pu être construite pour « {shipment.reference} ».",
                remedy_fr="Aucune analyse n'est produite.",
            )

        profile = profile_for(requires_cold_chain=bool(requires_cold_chain))
        ranking = rank_candidates(
            list(options), criteria=LOGISTICS_CRITERIA, profile=profile
        )
        by_id = {option.id: option for option in options}
        current = by_id["plan-actuel"]
        recommended = (
            by_id.get(ranking.recommended.id) if ranking.recommended is not None else None
        )

        return LogisticsOutcome(
            decision=self._to_decision(
                context=context,
                mode=shipment.transport_mode,
                ranking=ranking,
                current=current,
                recommended=recommended,
                weather=weather,
            ),
            ranking=ranking,
            alternatives=tuple(options),
            current_plan=current,
            recommended=recommended,
        )

    # --- résolution -------------------------------------------------------

    async def _shipment(self, reference: str) -> Shipment:
        shipment = (
            await self._session.execute(
                select(Shipment).where(Shipment.reference == reference.upper())
            )
        ).scalar_one_or_none()
        if shipment is None:
            raise NotFoundError(
                f"Expédition « {reference} » introuvable.",
                remedy_fr="Vérifiez la référence dans la page Expéditions.",
            )
        return shipment

    async def _sites(self, shipment: Shipment) -> tuple[Site, Site]:
        sites = {
            site.id: site
            for site in (await self._session.execute(select(Site))).scalars()
        }
        return sites[shipment.origin_site_id], sites[shipment.destination_site_id]

    async def _product(self, shipment: Shipment) -> tuple[str | None, bool | None]:
        if shipment.product_id is None:
            return None, None
        product = (
            await self._session.execute(
                select(Product).where(Product.id == shipment.product_id)
            )
        ).scalar_one_or_none()
        if product is None:
            return None, None
        if product.crop_id is None:
            return product.name_fr, None
        crop = (
            await self._session.execute(select(Crop).where(Crop.id == product.crop_id))
        ).scalar_one_or_none()
        return product.name_fr, (crop.requires_cold_chain if crop else None)

    # --- météo ------------------------------------------------------------

    async def _weather_along(self, routes: list[RouteGeometry]) -> WeatherAlongRoute:
        """Une série par cellule de grille traversée par l'un des itinéraires.

        Échantillonner par cellule plutôt que par tronçon évite d'interroger le
        fournisseur plusieurs fois pour le même quart de degré, ce qui rendrait
        la même valeur en coûtant davantage.

        Une cellule dont l'appel échoue reste **absente**, jamais remplie d'une
        valeur voisine : un tronçon non évalué doit se distinguer d'un tronçon
        sans risque.
        """
        wanted: dict[str, Coordinates] = {}
        for route in routes:
            for segment in route.segments:
                midpoint = interpolate(
                    Coordinates(segment.from_latitude, segment.from_longitude),
                    Coordinates(segment.to_latitude, segment.to_longitude),
                    0.5,
                )
                wanted.setdefault(WeatherAlongRoute.cell_key(midpoint), midpoint)

        by_cell: dict[str, list[HourlyConditions]] = {}
        for key, point in wanted.items():
            try:
                rows = await self._weather.hourly(
                    point.latitude, point.longitude, hours=FORECAST_HOURS
                )
            except Exception as exc:
                logger.warning("weather_cell_unavailable", cell=key, error=str(exc))
                continue
            by_cell[key] = [_to_conditions(row) for row in rows]
        return WeatherAlongRoute(by_cell)

    # --- mise en forme ----------------------------------------------------

    def _to_decision(
        self,
        *,
        context: ShipmentContext,
        mode: TransportMode,
        ranking: RankingResult,
        current: TransportAlternative,
        recommended: TransportAlternative | None,
        weather: WeatherAlongRoute,
    ) -> Decision:
        """Met en forme le contrat commun. Ne calcule rien."""
        assessment = current.assessment
        state = weather.dominant_state
        origin = (
            DataOrigin.SEED_DEMO if state is DataState.SIMULATED else DataOrigin.EXTERNAL_API
        )

        inputs: list[DecisionInput] = [
            DecisionInput(
                key="volume",
                label_fr="Volume expédié",
                value=context.volume_tonnes,
                unit="t",
                state=DataState.OBSERVED,
                origin=DataOrigin.MANUAL_ENTRY,
            ),
            DecisionInput(
                key="route_distance",
                label_fr="Distance du trajet prévu",
                value=assessment.route.total_distance_km if assessment else None,
                unit="km",
                state=DataState.DERIVED,
                origin=DataOrigin.REFERENCE_TABLE,
                source=SOURCE_ROAD_NETWORK,
            ),
            DecisionInput(
                key="road_reliability",
                label_fr="Fiabilité structurelle de l'axe",
                value=round(assessment.reliability, 3) if assessment else None,
                unit=None,
                state=DataState.DERIVED,
                origin=DataOrigin.REFERENCE_TABLE,
                source=SOURCE_ROAD_NETWORK,
                missing_reason_fr=(
                    None
                    if assessment
                    else "Aucun itinéraire évalué pour le plan actuel."
                ),
            ),
            DecisionInput(
                key="weather_cells",
                label_fr="Cellules météo échantillonnées",
                value=len(weather.cells),
                unit=None,
                state=state,
                origin=origin,
            ),
        ]

        steps: list[str] = []
        if assessment is not None:
            steps.append(
                f"1. Itinéraire retenu comme plan actuel : {assessment.route.label_fr}, "
                f"{fr(assessment.route.total_distance_km, 0)} km, "
                f"{fr(assessment.nominal_duration_hours, 2)} h nominales."
            )
            steps.append(
                f"2. Position du véhicule calculée tronçon par tronçon depuis un départ "
                f"le {context.departure_at:%d/%m à %H:%M} ; chaque tronçon est confronté "
                "aux conditions attendues **pendant** sa traversée."
            )
            exposed = [s for s in assessment.segments if s.is_exposed]
            if exposed:
                first = exposed[0]
                steps.append(
                    f"3. {fr(len(exposed), 0)} tronçon(s) exposé(s), soit "
                    f"{fr(assessment.exposed_distance_km, 0)} km "
                    f"({fr(assessment.exposure_fraction * 100, 0)} % du trajet). Premier : "
                    f"{first.from_name_fr} → {first.to_name_fr} ({first.road_ref}), "
                    f"traversé {fr(first.hours_from_departure, 1)} h après le départ."
                )
            else:
                steps.append(
                    "3. Aucun tronçon exposé sur la fenêtre de passage prévue : le "
                    "véhicule ne se trouve dans aucune zone perturbée au moment où il "
                    "l'emprunte."
                )
            steps.append(
                f"4. Durée ajustée du ralentissement attendu : "
                f"{fr(assessment.adjusted_duration_hours, 2)} h, arrivée estimée le "
                f"{assessment.estimated_arrival_at:%d/%m à %H:%M}."
            )
            steps.append(
                f"5. Coût du plan actuel : {fr(assessment.cost.total_mad, 0)} MAD pour "
                f"{fr(assessment.cost.trucks_required, 0)} camion(s) "
                f"({mode.label_fr.lower()})."
            )
        steps.append(
            f"{len(steps) + 1}. {fr(len(ranking.alternatives), 0)} option(s) examinée(s), "
            f"classées selon le profil « {ranking.profile.label_fr} »."
        )

        evidence = [
            EvidenceItem(
                label_fr="Réseau routier",
                detail_fr=(
                    "Distances routières de référence entre villes marocaines. "
                    "Suffisantes pour comparer des corridors, insuffisantes pour du "
                    "guidage."
                ),
                state=DataState.DERIVED,
                source=SOURCE_ROAD_NETWORK,
            ),
            EvidenceItem(
                label_fr="Conditions attendues",
                detail_fr=(
                    f"{fr(len(weather.cells), 0)} cellule(s) échantillonnée(s) le long "
                    f"des itinéraires, sur {fr(FORECAST_HOURS, 0)} h."
                ),
                state=state,
            ),
            EvidenceItem(
                label_fr="Pondération de l'arbitrage",
                detail_fr=ranking.profile.rationale_fr,
                state=DataState.DERIVED,
                source=SOURCE_RISK_ENGINE,
            ),
        ]

        warnings = [
            "L'indicateur de perturbation est dérivé de règles explicites. Ce n'est "
            "pas une probabilité calibrée : aucun historique d'incidents marocains "
            "n'a été utilisé pour l'établir.",
            "Les seuils de vigilance routière sont des valeurs de départ, à confirmer "
            "avec les gestionnaires de voirie.",
        ]
        if assessment is not None and any(s.unevaluated for s in assessment.segments):
            count = sum(1 for s in assessment.segments if s.unevaluated)
            warnings.append(
                f"{fr(count, 0)} tronçon(s) sans donnée météo : non évalués, ce qui "
                "n'est pas une absence de risque."
            )

        headline, outcome_code = _headline(current, recommended)
        return Decision(
            domain=DecisionDomain.LOGISTICS,
            subject_id=context.reference,
            subject_label_fr=(
                f"{context.reference} — {context.origin_name_fr} → "
                f"{context.destination_name_fr}"
            ),
            headline_fr=headline,
            outcome_code=outcome_code,
            inputs=tuple(inputs),
            calculation_steps_fr=tuple(steps),
            assumptions_fr=(
                f"Produit : {context.product_name_fr}. Mode : {mode.label_fr}. "
                f"Profil d'arbitrage : {ranking.profile.label_fr}.",
            ),
            alternatives=ranking.alternatives,
            evidence=tuple(evidence),
            tradeoffs_fr=ranking.caveats_fr,
            warnings_fr=tuple(warnings),
            reliability=reliability_level(_reliability_score(state)),
        )


def _reliability_score(state: DataState) -> float:
    """Une analyse ne peut pas être plus fiable que sa météo.

    Rendue par la même échelle que le reste du produit : une organisation de
    démonstration ne peut pas afficher « Fiabilité : Élevée » sur un
    réacheminement, pas plus que sur une irrigation.
    """
    return 0.30 if state is DataState.SIMULATED else 0.60


def _headline(
    current: TransportAlternative, recommended: TransportAlternative | None
) -> tuple[str, str]:
    """La conclusion en une phrase, et son code stable."""
    if recommended is None:
        return (
            "Aucune option ne respecte l'ensemble des contraintes : une décision "
            "humaine est nécessaire.",
            "NO_FEASIBLE_OPTION",
        )
    if recommended.id == current.id:
        assessment = current.assessment
        level = assessment.risk_level if assessment else RiskLevel.LOW
        if level.rank >= RiskLevel.MODERATE.rank:
            return (
                f"Maintenir le plan actuel malgré une exposition {level.label_fr.lower()} : "
                "aucune option examinée ne fait mieux.",
                "KEEP_CURRENT_PLAN",
            )
        return ("Maintenir le plan actuel : aucune exposition détectée.", "KEEP_CURRENT_PLAN")

    cost_delta = None
    if recommended.total_cost_mad is not None and current.total_cost_mad is not None:
        cost_delta = recommended.total_cost_mad - current.total_cost_mad
    suffix = ""
    if cost_delta is not None:
        suffix = (
            " sans surcoût."
            if abs(cost_delta) < 1
            else f" pour {fr(abs(cost_delta), 0)} MAD "
            f"{'de plus' if cost_delta > 0 else 'de moins'}."
        )
    return (f"{recommended.label_fr}{suffix}", recommended.kind)


def _to_conditions(row: HourlyWeather) -> HourlyConditions:
    """Traduit l'adaptateur vers le vocabulaire du moteur.

    Deux types plutôt qu'un : `domain/` ne doit pas dépendre de la forme d'une
    réponse de fournisseur, sinon un changement d'API météo remonterait jusqu'au
    cœur agronomique.
    """
    return HourlyConditions(
        at=row.at,
        precipitation_mm=row.precipitation_mm,
        wind_gust_kmh=row.wind_gust_kmh,
        temperature_c=row.temperature_c,
        state=row.state,
        origin=row.origin,
    )
