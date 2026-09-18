"""Exposition d'un itinéraire, tronçon par tronçon **et dans le temps**.

C'est l'apport central du moteur, et la raison pour laquelle il vaut d'être
porté plutôt que réécrit.

Un itinéraire n'est pas exposé parce qu'il traverse une région où il pleuvra dans
trente heures. Il est exposé si le véhicule s'y trouve **pendant** la fenêtre de
perturbation. Un camion qui franchit le col dans deux heures ne rencontre pas
l'orage prévu pour la nuit suivante.

Sans cette dimension temporelle, le système surestimerait massivement le risque,
alerterait sur des trajets déjà terminés, et cesserait d'être cru au bout de
quelques jours — ce qui est pire qu'un système absent, parce qu'on aurait cessé
de regarder ailleurs.

**Ce module est pur.** La météo lui est donnée. Le code d'origine appelait un
registre global de fournisseurs depuis la couche service, ce qui rendait
l'exposition intestable sans réseau ni monkeypatch : la partie intéressante — le
déplacement du véhicule face à une fenêtre — n'avait aucun test.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.enums import Confidence, DataOrigin, DataState, RiskLevel, TransportMode
from app.domain.formatting import fr
from app.domain.geo import Coordinates, interpolate
from app.domain.logistics.network import RouteGeometry, RouteSegment
from app.domain.logistics.road_risk import (
    FROST,
    RAIN_ACCUMULATION,
    RAIN_INTENSITY,
    WIND_GUST,
    vulnerability_of,
)
from app.domain.logistics.transport import CostBreakdown, estimate_cost
from app.domain.provenance import DataSourceRef

__all__ = [
    "HourlyConditions",
    "RouteAssessment",
    "SegmentExposure",
    "WeatherAlongRoute",
    "assess_route",
]

#: Ralentissement maximal du trafic sous conditions critiques. Volontairement
#: prudent : une pluie forte ralentit réellement un poids lourd, mais surestimer
#: ce ralentissement fabriquerait de faux dépassements d'échéance — et une alerte
#: fausse coûte plus cher qu'une alerte absente, parce qu'elle décrédibilise les
#: suivantes.
MAX_SPEED_REDUCTION = 0.35

#: Résolution de l'échantillonnage météo. Les modèles météo ont eux-mêmes une
#: résolution de cet ordre : interroger plus finement donnerait la même valeur en
#: coûtant davantage.
SAMPLING_GRID_DEGREES = 0.25

#: Un véhicule n'entre pas dans un tronçon à la seconde près.
WINDOW_MARGIN = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class HourlyConditions:
    """Conditions attendues sur une heure, en un point."""

    at: datetime
    precipitation_mm: float | None = None
    wind_gust_kmh: float | None = None
    temperature_c: float | None = None
    state: DataState = DataState.FORECAST
    origin: DataOrigin = DataOrigin.EXTERNAL_API


class WeatherAlongRoute:
    """Séries horaires indexées par cellule de grille.

    Une cellule sans série est **non évaluée**, jamais « sans risque » :
    l'absence de donnée n'est pas une absence de danger, et les deux doivent se
    distinguer à l'écran.
    """

    def __init__(self, by_cell: Mapping[str, Sequence[HourlyConditions]]) -> None:
        self._by_cell = {key: tuple(value) for key, value in by_cell.items()}

    @staticmethod
    def cell_key(point: Coordinates) -> str:
        lat = round(point.latitude / SAMPLING_GRID_DEGREES) * SAMPLING_GRID_DEGREES
        lon = round(point.longitude / SAMPLING_GRID_DEGREES) * SAMPLING_GRID_DEGREES
        return f"{lat:.2f}:{lon:.2f}"

    def series_for(self, point: Coordinates) -> tuple[HourlyConditions, ...] | None:
        return self._by_cell.get(self.cell_key(point))

    @property
    def cells(self) -> tuple[str, ...]:
        return tuple(self._by_cell)

    @property
    def dominant_state(self) -> DataState:
        """L'état le moins fiable rencontré.

        Une seule cellule simulée suffit à empêcher l'itinéraire de se présenter
        comme reposant sur des prévisions réelles.
        """
        states = [h.state for series in self._by_cell.values() for h in series]
        if any(s is DataState.SIMULATED for s in states):
            return DataState.SIMULATED
        return DataState.FORECAST


@dataclass(frozen=True, slots=True)
class SegmentExposure:
    """Exposition d'un tronçon sur la fenêtre où le véhicule l'emprunte."""

    index: int
    from_name_fr: str
    to_name_fr: str
    road_ref: str
    distance_km: float
    entry_at: datetime
    exit_at: datetime
    hours_from_departure: float

    rain_intensity_mm_h: float | None
    antecedent_rain_24h_mm: float | None
    max_wind_gust_kmh: float | None
    min_temperature_c: float | None

    level: RiskLevel
    severity: float
    reasons_fr: tuple[str, ...]
    #: Vrai quand aucune série météo ne couvre ce tronçon. Distinct d'un
    #: `level == LOW` : l'un dit « rien à signaler », l'autre « je ne sais pas ».
    unevaluated: bool

    @property
    def is_exposed(self) -> bool:
        return self.level.rank >= RiskLevel.MODERATE.rank


@dataclass(frozen=True, slots=True)
class RouteAssessment:
    """Évaluation complète d'un itinéraire pour une expédition donnée."""

    route: RouteGeometry
    departure_at: datetime
    estimated_arrival_at: datetime
    nominal_duration_hours: float
    adjusted_duration_hours: float

    risk_score: float
    risk_level: RiskLevel
    #: Indicateur **comparatif**, dérivé de règles explicites. Ce n'est pas une
    #: probabilité calibrée sur un historique d'incidents marocains : nous n'en
    #: disposons pas. Son usage légitime est de classer des itinéraires entre
    #: eux, et l'interface le dit.
    disruption_indicator: float
    reliability: float

    cost: CostBreakdown
    sla_deadline_at: datetime | None
    sla_compliant: bool
    sla_margin_hours: float | None

    segments: tuple[SegmentExposure, ...]
    main_risk_factors_fr: tuple[str, ...]
    confidence: Confidence
    data_state: DataState
    sources: tuple[DataSourceRef, ...]

    @property
    def exposed_distance_km(self) -> float:
        return round(sum(s.distance_km for s in self.segments if s.is_exposed), 1)

    @property
    def exposure_fraction(self) -> float:
        """Part du trajet réellement exposée — plus parlant qu'un score abstrait."""
        total = self.route.total_distance_km
        return round(self.exposed_distance_km / total, 3) if total else 0.0

    @property
    def first_exposure(self) -> SegmentExposure | None:
        return next((s for s in self.segments if s.is_exposed), None)


def assess_route(
    route: RouteGeometry,
    weather: WeatherAlongRoute,
    *,
    departure_at: datetime,
    volume_tonnes: float,
    transport_mode: TransportMode,
    sla_deadline_at: datetime | None,
    sources: tuple[DataSourceRef, ...],
) -> RouteAssessment:
    """Déplace le véhicule dans le temps le long du tracé et l'expose."""
    segments: list[SegmentExposure] = []
    cumulative_hours = 0.0

    for index, segment in enumerate(route.segments):
        entry_at = departure_at + timedelta(hours=cumulative_hours)
        exit_at = entry_at + timedelta(hours=segment.duration_hours)
        midpoint = interpolate(
            Coordinates(segment.from_latitude, segment.from_longitude),
            Coordinates(segment.to_latitude, segment.to_longitude),
            0.5,
        )
        exposure = _evaluate_segment(
            index=index,
            segment=segment,
            series=weather.series_for(midpoint),
            entry_at=entry_at,
            exit_at=exit_at,
            hours_from_departure=cumulative_hours,
        )
        segments.append(exposure)

        # Le ralentissement d'un tronçon décale l'heure de passage sur **tous**
        # les suivants. Cumuler la durée nominale placerait le véhicule au mauvais
        # endroit au mauvais moment, et l'exposition calculée porterait sur une
        # fenêtre qu'il ne traverse pas.
        cumulative_hours += segment.duration_hours * (
            1 + MAX_SPEED_REDUCTION * exposure.severity
        )

    frozen_segments = tuple(segments)
    risk_score = _aggregate(frozen_segments)
    arrival = departure_at + timedelta(hours=cumulative_hours)
    cost = estimate_cost(
        distance_km=route.total_distance_km,
        duration_hours=cumulative_hours,
        volume_tonnes=volume_tonnes,
        mode=transport_mode,
    )

    sla_margin: float | None = None
    sla_compliant = True
    if sla_deadline_at is not None:
        sla_margin = round((sla_deadline_at - arrival).total_seconds() / 3600, 2)
        sla_compliant = sla_margin >= 0

    return RouteAssessment(
        route=route,
        departure_at=departure_at,
        estimated_arrival_at=arrival,
        nominal_duration_hours=route.total_duration_hours,
        adjusted_duration_hours=round(cumulative_hours, 2),
        risk_score=risk_score,
        risk_level=RiskLevel.from_severity(risk_score),
        disruption_indicator=_disruption_indicator(risk_score, route.average_reliability),
        reliability=round(route.average_reliability, 3),
        cost=cost,
        sla_deadline_at=sla_deadline_at,
        sla_compliant=sla_compliant,
        sla_margin_hours=sla_margin,
        segments=frozen_segments,
        main_risk_factors_fr=_main_factors(frozen_segments),
        confidence=_confidence(frozen_segments, weather.dominant_state),
        data_state=weather.dominant_state,
        sources=sources,
    )


# --- interne ---------------------------------------------------------------


def _window(
    series: tuple[HourlyConditions, ...], start: datetime, end: datetime
) -> list[HourlyConditions]:
    return [h for h in series if start <= h.at <= end]


def _sum_precipitation(
    series: tuple[HourlyConditions, ...], start: datetime, end: datetime
) -> float | None:
    values = [h.precipitation_mm for h in _window(series, start, end)]
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _extreme(
    series: tuple[HourlyConditions, ...],
    start: datetime,
    end: datetime,
    read: Callable[[HourlyConditions], float | None],
    *,
    highest: bool,
) -> float | None:
    """Extremum d'une grandeur sur la fenêtre, ou `None` si elle manque partout.

    La grandeur est lue par une fonction plutôt que par un nom d'attribut :
    `getattr` rendrait `Any`, et le vérificateur cesserait de voir qu'une faute
    de frappe dans le nom d'un champ produit silencieusement « aucune donnée »,
    c'est-à-dire « aucun risque ».
    """
    present = [
        value for value in (read(h) for h in _window(series, start, end)) if value is not None
    ]
    if not present:
        return None
    return max(present) if highest else min(present)


def _evaluate_segment(
    *,
    index: int,
    segment: RouteSegment,
    series: tuple[HourlyConditions, ...] | None,
    entry_at: datetime,
    exit_at: datetime,
    hours_from_departure: float,
) -> SegmentExposure:
    def build(
        *,
        rain_intensity_mm_h: float | None,
        antecedent_rain_24h_mm: float | None,
        max_wind_gust_kmh: float | None,
        min_temperature_c: float | None,
        level: RiskLevel,
        severity: float,
        reasons_fr: tuple[str, ...],
        unevaluated: bool,
    ) -> SegmentExposure:
        """Les champs communs, nommés une fois.

        Un dictionnaire déballé par `**` conviendrait à l'exécution et
        n'apporterait aucune vérification : c'est exactement là qu'un champ
        renommé d'un côté se perd en silence.
        """
        return SegmentExposure(
            index=index,
            from_name_fr=segment.from_name_fr,
            to_name_fr=segment.to_name_fr,
            road_ref=segment.road_ref,
            distance_km=segment.distance_km,
            entry_at=entry_at,
            exit_at=exit_at,
            hours_from_departure=round(hours_from_departure, 2),
            rain_intensity_mm_h=rain_intensity_mm_h,
            antecedent_rain_24h_mm=antecedent_rain_24h_mm,
            max_wind_gust_kmh=max_wind_gust_kmh,
            min_temperature_c=min_temperature_c,
            level=level,
            severity=severity,
            reasons_fr=reasons_fr,
            unevaluated=unevaluated,
        )

    if not series:
        return build(
            rain_intensity_mm_h=None,
            antecedent_rain_24h_mm=None,
            max_wind_gust_kmh=None,
            min_temperature_c=None,
            level=RiskLevel.LOW,
            severity=0.0,
            reasons_fr=(
                "Conditions non évaluées : aucune donnée météo pour ce tronçon. "
                "Ce n'est pas une absence de risque.",
            ),
            unevaluated=True,
        )

    window_start = entry_at - WINDOW_MARGIN
    window_end = exit_at + WINDOW_MARGIN
    window_hours = max(0.5, (window_end - window_start).total_seconds() / 3600)

    traversal_rain = _sum_precipitation(series, window_start, window_end)
    intensity = traversal_rain / window_hours if traversal_rain is not None else None
    antecedent = _sum_precipitation(series, entry_at - timedelta(hours=24), entry_at)
    gust = _extreme(
        series, window_start, window_end, lambda h: h.wind_gust_kmh, highest=True
    )
    coldest = _extreme(
        series, window_start, window_end, lambda h: h.temperature_c, highest=False
    )

    severity = 0.0
    reasons: list[str] = []
    for criterion, value in (
        (RAIN_INTENSITY, intensity),
        (RAIN_ACCUMULATION, antecedent),
        (WIND_GUST, gust),
        (FROST, coldest),
    ):
        if value is None or criterion.evaluate(value) is RiskLevel.LOW:
            continue
        severity = max(severity, criterion.severity(value))
        reasons.append(
            f"{criterion.label_fr} : {fr(value, 1)} {criterion.unit} lors du passage "
            f"prévu entre {entry_at:%d/%m %H:%M} et {exit_at:%H:%M} "
            f"(seuil de vigilance {fr(criterion.moderate_at, 0)} {criterion.unit})."
        )

    if severity > 0:
        # La vulnérabilité de l'axe amplifie ou atténue : une régionale de
        # montagne se coupe là où une autoroute drainée tient.
        severity = min(1.0, severity * vulnerability_of(segment.road_class))
        severity = min(1.0, severity * (1 + (1 - segment.reliability)))
        if segment.notes_fr:
            reasons.append(f"{segment.notes_fr}.")

    return build(
        rain_intensity_mm_h=round(intensity, 2) if intensity is not None else None,
        antecedent_rain_24h_mm=round(antecedent, 1) if antecedent is not None else None,
        max_wind_gust_kmh=round(gust, 0) if gust is not None else None,
        min_temperature_c=round(coldest, 1) if coldest is not None else None,
        level=RiskLevel.from_severity(severity),
        severity=round(severity, 3),
        reasons_fr=tuple(reasons),
        unevaluated=False,
    )


def _aggregate(segments: tuple[SegmentExposure, ...]) -> float:
    """Risque global d'un itinéraire.

    Pondéré par la distance — un tronçon critique de 5 km ne compromet pas un
    trajet de 600 km autant qu'un tronçon critique de 200 km. Mais une moyenne
    pure diluerait un point de blocage ponctuel jusqu'à l'invisibilité, et un col
    coupé arrête le camion quelle que soit sa longueur.

    On retient donc le maximum des deux lectures : la moyenne pondérée, et le
    pire tronçon ramené à 70 % de sa sévérité.
    """
    if not segments:
        return 0.0
    total_km = sum(s.distance_km for s in segments) or 1.0
    weighted = sum(s.severity * s.distance_km for s in segments) / total_km
    worst = max((s.severity for s in segments), default=0.0)
    return round(min(1.0, max(weighted, worst * 0.7)), 3)


def _disruption_indicator(risk_score: float, reliability: float) -> float:
    """Indicateur comparatif dérivé de règles explicites.

    **Pas** une probabilité. Nommé `indicator` et non `probability` pour que
    l'appelant ne puisse pas s'y tromper : l'ancien nom invitait à l'afficher
    comme « 34 % de risque de perturbation », ce qu'aucune donnée ne soutient.
    """
    base = risk_score * 0.85
    fragility = (1 - reliability) * 0.5
    return round(min(0.95, base + fragility * risk_score + 0.02), 3)


def _main_factors(segments: tuple[SegmentExposure, ...]) -> tuple[str, ...]:
    """Trois raisons, les plus sévères d'abord."""
    ordered = sorted(
        (s for s in segments if s.is_exposed), key=lambda s: s.severity, reverse=True
    )
    factors: list[str] = []
    for segment in ordered:
        for reason in segment.reasons_fr:
            entry = f"{segment.from_name_fr} → {segment.to_name_fr} ({segment.road_ref}) : {reason}"
            if entry not in factors:
                factors.append(entry)
        if len(factors) >= 3:
            break
    return tuple(factors[:3])


def _confidence(segments: tuple[SegmentExposure, ...], state: DataState) -> Confidence:
    if state is DataState.SIMULATED:
        # Une donnée simulée ne peut pas fonder une confiance élevée, quelle que
        # soit la couverture.
        return Confidence.LOW
    if not segments:
        return Confidence.LOW
    unevaluated = sum(1 for s in segments if s.unevaluated)
    if unevaluated / len(segments) > 0.3:
        return Confidence.LOW
    return Confidence.MEDIUM if unevaluated else Confidence.HIGH
