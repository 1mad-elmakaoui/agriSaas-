"""Les moteurs logistiques : réseau, exposition, alternatives.

Le code d'origine n'avait aucun test sur la partie qui compte — le déplacement
du véhicule dans le temps face à une fenêtre de perturbation — parce que
l'évaluation appelait un registre global de fournisseurs météo. Le portage rend
ces moteurs purs, et c'est ce fichier qui en tire le bénéfice : la météo est
donnée, donc le comportement est reproductible à la minute près.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.enums import Confidence, DataState, RiskLevel, TransportMode
from app.domain.logistics.alternatives import (
    DEPARTURE_SHIFTS_HOURS,
    MIN_PREPARATION_HOURS,
    ShipmentContext,
    generate_alternatives,
    profile_for,
)
from app.domain.logistics.exposure import (
    HourlyConditions,
    WeatherAlongRoute,
    assess_route,
)
from app.domain.logistics.network import (
    MAX_DURATION_RATIO,
    RoutingError,
    alternative_routes,
    shortest_route,
)
from app.domain.logistics.transport import estimate_cost
from app.domain.provenance import DataSourceRef

SOURCE = DataSourceRef(id="test", label_fr="Test", kind="model")
DEPARTURE = datetime(2026, 9, 10, 5, 0, tzinfo=UTC)
DEADLINE = datetime(2026, 9, 10, 21, 0, tzinfo=UTC)


def _calm() -> WeatherAlongRoute:
    """Météo clémente partout : aucun critère franchi."""
    return _uniform(precipitation_mm=0.0, wind_gust_kmh=15.0, temperature_c=22.0)


def _uniform(**values: float) -> WeatherAlongRoute:
    route = shortest_route("AGADIR", "CASABLANCA")
    by_cell: dict[str, list[HourlyConditions]] = {}
    start = DEPARTURE - timedelta(hours=30)
    for segment in route.segments:
        for latitude, longitude in (
            (segment.from_latitude, segment.from_longitude),
            (segment.to_latitude, segment.to_longitude),
            ((segment.from_latitude + segment.to_latitude) / 2,
             (segment.from_longitude + segment.to_longitude) / 2),
        ):
            from app.domain.geo import Coordinates

            key = WeatherAlongRoute.cell_key(Coordinates(latitude, longitude))
            by_cell.setdefault(
                key,
                [
                    HourlyConditions(at=start + timedelta(hours=h), **values)
                    for h in range(80)
                ],
            )
    return WeatherAlongRoute(by_cell)


def _storm_between(start_hour: int, end_hour: int) -> WeatherAlongRoute:
    """Un front de pluie intense, sur une fenêtre horaire donnée, partout.

    « Partout » est délibéré : le test porte sur la position du véhicule **dans
    le temps**, pas sur sa position géographique. Un front localisé mêlerait les
    deux effets.
    """
    route = shortest_route("AGADIR", "CASABLANCA")
    from app.domain.geo import Coordinates

    by_cell: dict[str, list[HourlyConditions]] = {}
    base = DEPARTURE - timedelta(hours=30)
    for segment in route.segments:
        for latitude, longitude in (
            (segment.from_latitude, segment.from_longitude),
            ((segment.from_latitude + segment.to_latitude) / 2,
             (segment.from_longitude + segment.to_longitude) / 2),
            (segment.to_latitude, segment.to_longitude),
        ):
            key = WeatherAlongRoute.cell_key(Coordinates(latitude, longitude))
            if key in by_cell:
                continue
            hours = []
            for h in range(80):
                at = base + timedelta(hours=h)
                offset = (at - DEPARTURE).total_seconds() / 3600
                storming = start_hour <= offset < end_hour
                hours.append(
                    HourlyConditions(
                        at=at,
                        precipitation_mm=25.0 if storming else 0.0,
                        wind_gust_kmh=95.0 if storming else 12.0,
                        temperature_c=16.0,
                    )
                )
            by_cell[key] = hours
    return WeatherAlongRoute(by_cell)


# ---------------------------------------------------------------------------
# Réseau
# ---------------------------------------------------------------------------
def test_the_fastest_route_is_not_the_shortest_one():
    """Le graphe minimise la **durée**, pas la distance.

    Un détour autoroutier plus long en kilomètres est souvent plus rapide, et
    c'est la durée qui compte pour une marchandise périssable. Un moteur qui
    minimiserait la distance renverrait le corridor littoral.
    """
    route = shortest_route("AGADIR", "CASABLANCA")
    assert "MARRAKECH" in route.node_codes
    assert route.total_duration_hours < 6.0


def test_alternatives_are_different_corridors_not_variants():
    """Trois itinéraires qui partagent le même corridor ne sont pas trois choix."""
    routes = alternative_routes("AGADIR", "CASABLANCA", max_routes=3)
    assert len(routes) >= 2

    corridors = [set(r.node_codes[1:-1]) for r in routes]
    for index, first in enumerate(corridors):
        for second in corridors[index + 1 :]:
            union = first | second
            overlap = len(first & second) / len(union) if union else 0.0
            assert overlap <= 0.7, "deux itinéraires décrivent le même corridor"

    # Le corridor littoral apparaît : c'est l'alternative que la démonstration
    # attend, et elle est moins fiable que l'A7.
    coastal = [r for r in routes if "SAFI" in r.node_codes]
    assert coastal
    assert coastal[0].average_reliability < routes[0].average_reliability


def test_an_absurd_detour_is_not_offered():
    """Mieux vaut deux alternatives crédibles que quatre dont deux absurdes."""
    routes = alternative_routes("AGADIR", "CASABLANCA", max_routes=4)
    best = min(r.total_duration_hours for r in routes)
    for route in routes:
        assert route.total_duration_hours <= best * MAX_DURATION_RATIO


def test_an_unknown_node_is_refused_rather_than_guessed():
    with pytest.raises(RoutingError, match="inconnu"):
        shortest_route("ATLANTIDE", "CASABLANCA")
    with pytest.raises(RoutingError, match="identiques"):
        shortest_route("AGADIR", "AGADIR")


def test_reliability_is_weighted_by_distance():
    """Une moyenne simple donnerait le même poids à 8 km urbains qu'à 175 km de montagne."""
    route = shortest_route("AGADIR", "CASABLANCA")
    simple = sum(s.reliability for s in route.segments) / len(route.segments)
    assert route.average_reliability != pytest.approx(simple)


# ---------------------------------------------------------------------------
# Exposition — la dimension temporelle
# ---------------------------------------------------------------------------
def test_a_storm_the_truck_never_meets_does_not_expose_it():
    """Le cœur du moteur : un orage prévu après l'arrivée n'expose rien.

    Sans cette dimension, le système alerterait sur des trajets déjà terminés et
    cesserait d'être cru — ce qui est pire qu'un système absent, parce qu'on
    aurait cessé de regarder ailleurs.
    """
    route = shortest_route("AGADIR", "CASABLANCA")
    late_storm = _storm_between(30, 40)  # bien après l'arrivée
    assessment = assess_route(
        route,
        late_storm,
        departure_at=DEPARTURE,
        volume_tonnes=180,
        transport_mode=TransportMode.ROAD_REFRIGERATED,
        sla_deadline_at=DEADLINE,
        sources=(SOURCE,),
    )
    assert assessment.risk_level is RiskLevel.LOW
    assert assessment.exposure_fraction == 0.0


def test_a_storm_during_the_crossing_exposes_the_route():
    route = shortest_route("AGADIR", "CASABLANCA")
    assessment = assess_route(
        route,
        _storm_between(0, 8),
        departure_at=DEPARTURE,
        volume_tonnes=180,
        transport_mode=TransportMode.ROAD_REFRIGERATED,
        sla_deadline_at=DEADLINE,
        sources=(SOURCE,),
    )
    assert assessment.risk_level.rank >= RiskLevel.MODERATE.rank
    assert assessment.exposure_fraction > 0
    assert assessment.first_exposure is not None
    # Le motif nomme la grandeur, sa valeur, le seuil et l'heure de passage.
    reason = assessment.first_exposure.reasons_fr[0]
    assert "seuil de vigilance" in reason
    assert "lors du passage prévu" in reason


def test_the_same_storm_is_avoided_by_leaving_earlier():
    """Le résultat que la démonstration attend, et il sort du calcul.

    Le front arrive : partir plus tôt fait passer le camion avant. Aucun réglage
    ne le décide — c'est la comparaison des deux expositions qui le montre.
    """
    route = shortest_route("AGADIR", "CASABLANCA")
    storm = _storm_between(4, 14)

    as_planned = assess_route(
        route, storm, departure_at=DEPARTURE, volume_tonnes=180,
        transport_mode=TransportMode.ROAD_REFRIGERATED,
        sla_deadline_at=DEADLINE, sources=(SOURCE,),
    )
    earlier = assess_route(
        route, storm, departure_at=DEPARTURE - timedelta(hours=6), volume_tonnes=180,
        transport_mode=TransportMode.ROAD_REFRIGERATED,
        sla_deadline_at=DEADLINE, sources=(SOURCE,),
    )
    assert as_planned.risk_score > earlier.risk_score


def test_a_slowed_segment_moves_the_truck_later_on_every_following_one():
    """Cumuler la durée nominale placerait le véhicule au mauvais moment.

    L'exposition porterait alors sur une fenêtre qu'il ne traverse pas — l'erreur
    la plus difficile à repérer, parce que le résultat reste plausible.
    """
    route = shortest_route("AGADIR", "CASABLANCA")
    calm = assess_route(
        route, _calm(), departure_at=DEPARTURE, volume_tonnes=180,
        transport_mode=TransportMode.ROAD_REFRIGERATED,
        sla_deadline_at=DEADLINE, sources=(SOURCE,),
    )
    rough = assess_route(
        route, _storm_between(0, 20), departure_at=DEPARTURE, volume_tonnes=180,
        transport_mode=TransportMode.ROAD_REFRIGERATED,
        sla_deadline_at=DEADLINE, sources=(SOURCE,),
    )
    assert calm.adjusted_duration_hours == pytest.approx(calm.nominal_duration_hours, abs=0.01)
    assert rough.adjusted_duration_hours > calm.adjusted_duration_hours
    assert rough.estimated_arrival_at > calm.estimated_arrival_at


def test_a_segment_without_weather_is_unevaluated_never_safe():
    """L'absence de donnée n'est pas une absence de risque, et se distingue."""
    route = shortest_route("AGADIR", "CASABLANCA")
    assessment = assess_route(
        route,
        WeatherAlongRoute({}),
        departure_at=DEPARTURE,
        volume_tonnes=180,
        transport_mode=TransportMode.ROAD_REFRIGERATED,
        sla_deadline_at=DEADLINE,
        sources=(SOURCE,),
    )
    assert all(s.unevaluated for s in assessment.segments)
    assert all("pas une absence de risque" in s.reasons_fr[0] for s in assessment.segments)
    # Couverture nulle : la confiance ne peut pas être élevée.
    assert assessment.confidence is Confidence.LOW


def test_simulated_weather_cannot_produce_high_confidence():
    route = shortest_route("AGADIR", "CASABLANCA")
    from app.domain.geo import Coordinates

    simulated = WeatherAlongRoute(
        {
            WeatherAlongRoute.cell_key(
                Coordinates(s.from_latitude, s.from_longitude)
            ): [
                HourlyConditions(
                    at=DEPARTURE + timedelta(hours=h),
                    precipitation_mm=0.0,
                    state=DataState.SIMULATED,
                )
                for h in range(40)
            ]
            for s in route.segments
        }
    )
    assessment = assess_route(
        route, simulated, departure_at=DEPARTURE, volume_tonnes=180,
        transport_mode=TransportMode.ROAD_REFRIGERATED,
        sla_deadline_at=DEADLINE, sources=(SOURCE,),
    )
    assert assessment.data_state is DataState.SIMULATED
    assert assessment.confidence is Confidence.LOW


def test_a_short_critical_segment_is_not_diluted_away():
    """Un col coupé arrête le camion, quelle que soit la longueur du tronçon.

    Une moyenne pondérée pure ferait disparaître un point de blocage ponctuel
    dans un trajet de 500 km.
    """
    route = shortest_route("AGADIR", "CASABLANCA")
    from app.domain.geo import Coordinates

    shortest_segment = min(route.segments, key=lambda s: s.distance_km)
    key = WeatherAlongRoute.cell_key(
        Coordinates(
            (shortest_segment.from_latitude + shortest_segment.to_latitude) / 2,
            (shortest_segment.from_longitude + shortest_segment.to_longitude) / 2,
        )
    )
    localised = WeatherAlongRoute(
        {
            key: [
                HourlyConditions(
                    at=DEPARTURE + timedelta(hours=h),
                    precipitation_mm=30.0,
                    wind_gust_kmh=110.0,
                    temperature_c=10.0,
                )
                for h in range(40)
            ]
        }
    )
    assessment = assess_route(
        route, localised, departure_at=DEPARTURE, volume_tonnes=180,
        transport_mode=TransportMode.ROAD_REFRIGERATED,
        sla_deadline_at=DEADLINE, sources=(SOURCE,),
    )
    exposed_km = assessment.exposed_distance_km
    assert exposed_km < route.total_distance_km * 0.2
    # Malgré une part de trajet faible, le risque global reste visible.
    assert assessment.risk_level.rank >= RiskLevel.MODERATE.rank


def test_the_disruption_figure_is_an_indicator_not_a_probability():
    """Le nom porte la limite : `indicator`, jamais `probability`.

    L'ancien nom invitait à l'afficher comme « 34 % de risque de perturbation »,
    ce qu'aucune donnée ne soutient — aucun historique d'incidents marocains n'a
    été utilisé pour le calibrer.
    """
    route = shortest_route("AGADIR", "CASABLANCA")
    assessment = assess_route(
        route, _calm(), departure_at=DEPARTURE, volume_tonnes=180,
        transport_mode=TransportMode.ROAD_REFRIGERATED,
        sla_deadline_at=DEADLINE, sources=(SOURCE,),
    )
    assert hasattr(assessment, "disruption_indicator")
    assert not hasattr(assessment, "disruption_probability")
    assert 0.0 <= assessment.disruption_indicator <= 0.95


# ---------------------------------------------------------------------------
# Coût
# ---------------------------------------------------------------------------
def test_fixed_costs_scale_with_the_number_of_trucks():
    """Un convoi de huit frigorifiques ne coûte pas les frais fixes d'un seul.

    Le code d'origine écrivait `fixed_cost_mad * trucks / max(1, trucks)`, qui
    vaut toujours `fixed_cost_mad` et contredit le commentaire qui
    l'accompagnait. Le portage tranche, et ce test fixe la décision.
    """
    one = estimate_cost(
        distance_km=100, duration_hours=2, volume_tonnes=20,
        mode=TransportMode.ROAD_REFRIGERATED,
    )
    many = estimate_cost(
        distance_km=100, duration_hours=2, volume_tonnes=180,
        mode=TransportMode.ROAD_REFRIGERATED,
    )
    assert one.trucks_required == 1
    assert many.trucks_required == 8
    assert many.fixed_cost_mad == one.fixed_cost_mad * 8


def test_the_truck_count_is_rounded_up_because_half_a_truck_cannot_be_hired():
    """L'arrondi crée des effets de seuil réels, que le modèle doit refléter."""
    just_under = estimate_cost(
        distance_km=100, duration_hours=2, volume_tonnes=24.0,
        mode=TransportMode.ROAD_REFRIGERATED,
    )
    just_over = estimate_cost(
        distance_km=100, duration_hours=2, volume_tonnes=24.1,
        mode=TransportMode.ROAD_REFRIGERATED,
    )
    assert just_under.trucks_required == 1
    assert just_over.trucks_required == 2


def test_a_mode_without_cost_parameters_refuses_rather_than_borrowing_them():
    """Ce qui manque manque : le rail ne reçoit pas les paramètres du camion."""
    with pytest.raises(ValueError, match="Aucun paramètre de coût"):
        estimate_cost(
            distance_km=100, duration_hours=2, volume_tonnes=20,
            mode=TransportMode.RAIL,
        )


def test_the_cost_breaks_down_into_lines_a_farmer_can_read():
    cost = estimate_cost(
        distance_km=542, duration_hours=5.1, volume_tonnes=180,
        mode=TransportMode.ROAD_REFRIGERATED,
    )
    labels = [line.label_fr for line in cost.lines_fr]
    assert any("Transport" in label for label in labels)
    assert any("Frais fixes" in label for label in labels)
    assert any("Groupe froid" in label for label in labels)
    assert round(sum(line.amount_mad for line in cost.lines_fr)) == round(cost.total_mad)


# ---------------------------------------------------------------------------
# Alternatives
# ---------------------------------------------------------------------------
def _context(**overrides: object) -> ShipmentContext:
    defaults: dict[str, object] = {
        "reference": "EXP-1842",
        "product_name_fr": "Tomate cerise export",
        "requires_cold_chain": True,
        "volume_tonnes": 180.0,
        "transport_mode": TransportMode.ROAD_REFRIGERATED,
        "origin_node_code": "AGADIR",
        "origin_name_fr": "Agadir",
        "destination_node_code": "CASABLANCA",
        "destination_name_fr": "Casablanca",
        "destination_capacity_tonnes": None,
        "departure_at": DEPARTURE,
        "sla_deadline_at": DEADLINE,
        "now": DEPARTURE - timedelta(hours=12),
    }
    defaults.update(overrides)
    return ShipmentContext(**defaults)  # type: ignore[arg-type]


def test_advancing_the_departure_is_among_the_options_examined():
    """Un moteur qui n'explorerait que les retards ne trouverait jamais la
    meilleure réponse à un front qui arrive."""
    assert any(shift < 0 for shift in DEPARTURE_SHIFTS_HOURS)

    routes = alternative_routes("AGADIR", "CASABLANCA", max_routes=3)
    options = generate_alternatives(_context(), routes, _calm(), sources=(SOURCE,))
    advanced = [o for o in options if o.id.startswith("depart-avance")]
    assert advanced


def test_a_departure_before_the_preparation_delay_is_rejected_with_its_numbers():
    """« Infaisable » ne se conteste pas ; « 1 h < 3 h requises » se conteste."""
    routes = alternative_routes("AGADIR", "CASABLANCA", max_routes=1)
    context = _context(now=DEPARTURE - timedelta(hours=1))
    options = generate_alternatives(context, routes, _calm(), sources=(SOURCE,))

    too_soon = [o for o in options if o.rejections and o.id.startswith("depart-avance")]
    assert too_soon
    reason = too_soon[0].rejections[0]
    assert reason.code == "preparation_time"
    assert reason.limit == MIN_PREPARATION_HOURS
    assert reason.unit == "h"
    # Rien n'est chiffré pour une option écartée : elle ne doit pas paraître
    # séduisante dans un tableau comparatif.
    assert too_soon[0].total_cost_mad is None


def test_an_option_arriving_after_the_deadline_is_infeasible_not_merely_worse():
    """Une contrainte dure filtre avant toute notation.

    Ranger un plan qui rate l'échéance, même en dernier, le met dans un tableau
    où l'utilisateur le lira comme un choix possible.
    """
    routes = alternative_routes("AGADIR", "CASABLANCA", max_routes=1)
    tight = _context(sla_deadline_at=DEPARTURE + timedelta(hours=4))
    options = generate_alternatives(tight, routes, _calm(), sources=(SOURCE,))

    late = [o for o in options if any(r.code == "sla_missed" for r in o.rejections)]
    assert late
    reason = next(r for r in late[0].rejections if r.code == "sla_missed")
    assert reason.observed is not None and reason.observed > 0
    assert "après l'échéance de service" in reason.message_fr


def test_insufficient_destination_capacity_blocks_every_route():
    routes = alternative_routes("AGADIR", "CASABLANCA", max_routes=2)
    cramped = _context(destination_capacity_tonnes=120.0)
    options = generate_alternatives(cramped, routes, _calm(), sources=(SOURCE,))

    blocked = [o for o in options if any(r.code == "destination_capacity" for r in o.rejections)]
    assert len(blocked) == len([o for o in options if o.assessment is not None])
    reason = blocked[0].rejections[0]
    assert reason.observed == 180.0
    assert reason.limit == 120.0


def test_the_profile_follows_the_product_not_a_setting():
    """Des tomates réfrigérées et du blé en vrac n'ont pas la même fonction objectif."""
    perishable = profile_for(requires_cold_chain=True)
    staple = profile_for(requires_cold_chain=False)
    assert perishable.weights["risk"] > staple.weights["risk"]
    assert staple.weights["cost_mad"] > perishable.weights["cost_mad"]
    for profile in (perishable, staple):
        assert sum(profile.weights.values()) == pytest.approx(1.0)


def test_the_current_plan_is_marked_so_the_comparison_has_a_baseline():
    routes = alternative_routes("AGADIR", "CASABLANCA", max_routes=2)
    options = generate_alternatives(_context(), routes, _calm(), sources=(SOURCE,))
    current = [o for o in options if o.is_current_plan]
    assert len(current) == 1
    assert current[0].id == "plan-actuel"
