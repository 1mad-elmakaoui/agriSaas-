"""Irrigation requirement, duration, cost and scenario tests."""

import pytest

from app.domain.irrigation.constants import Recommendation, StressLevel
from app.domain.irrigation.irrigation import (
    IrrigationSystemParameters,
    calculate_irrigation_duration,
    calculate_irrigation_requirement,
    calculate_water_cost,
    format_duration_fr,
)
from app.domain.irrigation.scenarios import compare_scenarios, simulate_irrigation_scenario

DRIP = IrrigationSystemParameters(
    code="drip", name_fr="Goutte-à-goutte", efficiency=0.90, flow_rate_m3_per_hour=14.0
)
FLOOD = IrrigationSystemParameters(code="flood", name_fr="Gravitaire", efficiency=0.60)


def _requirement(depletion, **kwargs):
    params = dict(
        current_depletion_mm=depletion,
        total_available_water_mm=120.0,
        readily_available_water_mm=60.0,
        etc_mm_day=5.0,
        field_area_ha=4.0,
        system=DRIP,
    )
    params.update(kwargs)
    return calculate_irrigation_requirement(**params)


def test_no_irrigation_when_reservoir_is_full():
    result = _requirement(5.0)
    assert result.recommendation is Recommendation.NO_IRRIGATION
    assert result.volume_m3 == 0.0


def test_irrigation_triggered_past_readily_available_water():
    result = _requirement(70.0)
    assert result.recommendation is Recommendation.IRRIGATE
    assert result.volume_m3 > 0


def test_monitor_state_just_below_the_trigger():
    # Deficit + 2 days of ETc lands between the monitor and trigger thresholds.
    result = _requirement(38.0)
    assert result.recommendation is Recommendation.MONITOR
    assert result.volume_m3 == 0.0


def test_projected_deficit_includes_the_planning_horizon():
    result = _requirement(50.0)
    assert result.crop_demand_horizon_mm == pytest.approx(5.0 * result.horizon_days)
    assert result.projected_depletion_mm == pytest.approx(50.0 + result.crop_demand_horizon_mm)


def test_effective_rainfall_reduces_the_requirement():
    dry = _requirement(70.0)
    rainy = _requirement(70.0, effective_rainfall_mm=15.0)
    assert rainy.net_requirement_mm == pytest.approx(dry.net_requirement_mm - 15.0)


def test_significant_forecast_rain_postpones_irrigation():
    result = _requirement(70.0, forecast_rainfall_postpones=True)
    assert result.recommendation is Recommendation.POSTPONE_RAIN
    assert result.volume_m3 == 0.0


def test_gross_requirement_accounts_for_efficiency():
    result = _requirement(70.0)
    assert result.gross_requirement_mm == pytest.approx(result.net_requirement_mm / 0.90)


def test_less_efficient_system_needs_more_water_for_the_same_crop():
    drip = _requirement(70.0, system=DRIP)
    flood = _requirement(70.0, system=FLOOD)
    assert flood.volume_m3 > drip.volume_m3
    assert flood.net_requirement_mm == pytest.approx(drip.net_requirement_mm)


def test_volume_conversion_one_mm_over_one_hectare_is_ten_cubic_metres():
    result = _requirement(70.0, field_area_ha=1.0)
    assert result.volume_m3 == pytest.approx(result.gross_requirement_mm * 10.0)
    assert result.volume_liters == pytest.approx(result.volume_m3 * 1000.0)


def test_volume_scales_linearly_with_area():
    small = _requirement(70.0, field_area_ha=2.0)
    large = _requirement(70.0, field_area_ha=8.0)
    assert large.volume_m3 == pytest.approx(small.volume_m3 * 4.0)


def test_dose_is_capped_by_soil_infiltration_capacity():
    result = _requirement(110.0, infiltration_rate_mm_per_hour=3.0)
    assert result.capped_by_infiltration is True
    assert result.net_requirement_mm == pytest.approx(3.0 * 4.0)
    assert any("fractionnez" in w.lower() for w in result.warnings)


def test_the_infiltration_cap_appears_in_the_trace_not_only_in_a_warning():
    """Un plafond est une étape du raisonnement, pas une note en marge.

    Sans elle, la trace passe de « dose nette 127,5 mm » à une dose brute
    calculée sur 48 mm, et le lecteur voit un nombre apparaître de nulle part —
    exactement au moment où il cherchait à comprendre.
    """
    result = _requirement(110.0, infiltration_rate_mm_per_hour=3.0)
    capping = [s for s in result.steps if "plafonnée" in s]
    assert capping, result.steps
    # L'étape nomme les deux nombres et d'où vient la limite.
    assert "12,0 mm" in capping[0]
    assert "3,0 mm/h" in capping[0]


def test_an_undocumented_infiltration_rate_is_announced_not_ignored():
    """Sans vitesse d'infiltration, la dose n'est pas plafonnée — et on le dit.

    Le silence serait le pire des trois comportements possibles : la valeur
    affichée serait indiscernable d'une dose vérifiée, alors qu'elle peut
    ruisseler entièrement. Aucune vitesse par défaut n'est substituée : elle
    varie d'un facteur dix entre un sable et une argile, donc une valeur
    « raisonnable » y serait une invention.
    """
    unchecked = _requirement(110.0)
    assert unchecked.capped_by_infiltration is False
    assert any("n'est pas documentée" in w for w in unchecked.warnings)

    # Documentée : le plafond s'applique, et l'avertissement d'absence disparaît.
    checked = _requirement(110.0, infiltration_rate_mm_per_hour=3.0)
    assert not any("n'est pas documentée" in w for w in checked.warnings)


def test_invalid_area_rejected():
    with pytest.raises(ValueError):
        _requirement(70.0, field_area_ha=0.0)


def test_invalid_efficiency_rejected():
    with pytest.raises(ValueError):
        IrrigationSystemParameters(code="x", name_fr="X", efficiency=1.5)


def test_every_step_is_traceable():
    result = _requirement(70.0)
    assert len(result.steps) >= 4
    assert any("Volume" in s for s in result.steps)


# ------------------------------------------------------------------ duration
def test_duration_is_volume_over_flow_rate():
    duration = calculate_irrigation_duration(volume_m3=31.2, flow_rate_m3_per_hour=14.0)
    assert duration.duration_hours == pytest.approx(31.2 / 14.0)
    assert duration.duration_minutes == pytest.approx(31.2 / 14.0 * 60)
    assert duration.duration_label_fr == "2h14"


def test_duration_is_none_without_a_flow_rate():
    assert calculate_irrigation_duration(volume_m3=31.2, flow_rate_m3_per_hour=None) is None
    assert calculate_irrigation_duration(volume_m3=31.2, flow_rate_m3_per_hour=0) is None


def test_pump_capacity_limits_the_usable_flow_rate():
    system = IrrigationSystemParameters(
        code="drip", name_fr="Goutte-à-goutte", efficiency=0.9,
        flow_rate_m3_per_hour=40.0, pump_capacity_m3_per_hour=15.0,
    )
    assert system.usable_flow_rate == 15.0


def test_duration_formatting_is_farmer_readable():
    assert format_duration_fr(134) == "2h14"
    assert format_duration_fr(120) == "2h00"
    assert format_duration_fr(45) == "45 min"


# ---------------------------------------------------------------------- cost
def test_cost_is_volume_times_tariff():
    cost = calculate_water_cost(volume_m3=31.2, cost_per_m3=1.5)
    assert cost.estimated_cost == pytest.approx(46.8)
    assert cost.currency == "MAD"


def test_cost_is_none_without_a_tariff():
    assert calculate_water_cost(volume_m3=31.2, cost_per_m3=None) is None


# ----------------------------------------------------------------- scenarios
def _comparison(**kwargs):
    params = dict(
        baseline_gross_volume_m3=310.0,
        baseline_net_mm=22.0,
        current_depletion_mm=26.0,
        total_available_water_mm=60.0,
        readily_available_water_mm=25.0,
        etc_mm_day=4.9,
        cost_per_m3=1.5,
        yield_response_factor_ky=1.05,
    )
    params.update(kwargs)
    return compare_scenarios(**params)


def test_scenarios_cover_the_documented_variations():
    comparison = _comparison()
    variations = [s.variation_pct for s in comparison.scenarios]
    assert 0.0 in variations
    for expected in (-0.10, -0.20, -0.30, 0.10, 0.20):
        assert expected in variations


def test_reducing_irrigation_saves_water_proportionally():
    comparison = _comparison()
    minus20 = next(s for s in comparison.scenarios if s.variation_pct == -0.20)
    assert minus20.volume_m3 == pytest.approx(310.0 * 0.80)
    assert minus20.water_saved_pct == pytest.approx(20.0)


def test_reducing_irrigation_never_reduces_stress():
    comparison = _comparison()
    severity = {
        StressLevel.NORMAL: 0, StressLevel.MODERATE: 1,
        StressLevel.HIGH: 2, StressLevel.CRITICAL: 3,
    }
    ordered = sorted(comparison.scenarios, key=lambda s: s.variation_pct)
    severities = [severity[s.worst_stress_level] for s in ordered]
    assert severities == sorted(severities, reverse=True)


def test_deficit_scenarios_end_drier():
    comparison = _comparison()
    baseline = next(s for s in comparison.scenarios if s.is_baseline)
    minus30 = next(s for s in comparison.scenarios if s.variation_pct == -0.30)
    assert minus30.final_depletion_mm > baseline.final_depletion_mm


def test_yield_impact_is_estimated_only_with_a_documented_ky():
    with_ky = _comparison(yield_response_factor_ky=1.05).scenarios[0]
    assert with_ky.yield_impact_pct is not None
    assert "ESTIMATION" in with_ky.yield_impact_note_fr

    without_ky = _comparison(yield_response_factor_ky=None).scenarios[0]
    assert without_ky.yield_impact_pct is None
    assert "non estimé" in without_ky.yield_impact_note_fr


def test_yield_loss_grows_as_irrigation_shrinks():
    comparison = _comparison()
    baseline = next(s for s in comparison.scenarios if s.is_baseline)
    minus30 = next(s for s in comparison.scenarios if s.variation_pct == -0.30)
    assert minus30.yield_impact_pct > baseline.yield_impact_pct


def test_cost_savings_are_reported_when_a_tariff_exists():
    minus20 = next(s for s in _comparison().scenarios if s.variation_pct == -0.20)
    assert minus20.cost_saved == pytest.approx(310.0 * 0.20 * 1.5)


def test_no_cost_reported_without_a_tariff():
    scenario = _comparison(cost_per_m3=None).scenarios[0]
    assert scenario.estimated_cost is None
    assert scenario.cost_saved is None


def test_simulation_is_deterministic():
    kwargs = dict(
        variation_pct=-0.20,
        baseline_gross_volume_m3=310.0,
        baseline_net_mm=22.0,
        current_depletion_mm=26.0,
        total_available_water_mm=60.0,
        readily_available_water_mm=25.0,
        etc_mm_day=4.9,
    )
    first = simulate_irrigation_scenario(**kwargs)
    second = simulate_irrigation_scenario(**kwargs)
    assert first.to_dict() == second.to_dict()


def test_scenario_assumptions_are_stated():
    comparison = _comparison()
    assert len(comparison.assumptions) >= 4
    assert any("Ks" in a for a in comparison.assumptions)


def test_variation_below_minus_one_hundred_percent_is_rejected():
    with pytest.raises(ValueError):
        simulate_irrigation_scenario(
            variation_pct=-1.5, baseline_gross_volume_m3=100.0, baseline_net_mm=10.0,
            current_depletion_mm=20.0, total_available_water_mm=60.0,
            readily_available_water_mm=25.0, etc_mm_day=5.0,
        )


def test_dose_never_exceeds_what_the_root_zone_can_hold():
    """The dose refills to field capacity — the horizon only sets the trigger.

    On a shallow soil the planning horizon can exceed the whole reservoir;
    applying it would push water below the roots.
    """
    result = _requirement(
        70.0, total_available_water_mm=75.0, readily_available_water_mm=30.0
    )
    assert result.recommendation is Recommendation.IRRIGATE
    assert result.net_requirement_mm == pytest.approx(70.0)
    assert result.net_requirement_mm <= 75.0
    # The projected deficit is still reported — it is what justified irrigating.
    assert result.projected_depletion_mm > result.net_requirement_mm


def test_trigger_uses_the_projected_deficit_but_the_dose_uses_todays():
    result = _requirement(55.0)
    assert result.recommendation is Recommendation.IRRIGATE
    assert result.projected_depletion_mm == pytest.approx(55.0 + 5.0 * result.horizon_days)
    assert result.net_requirement_mm == pytest.approx(55.0)


# ---------------------------------------------------------------------------
# Quota saisonnier — ajouté en phase 3 (décision 0002)
# ---------------------------------------------------------------------------
def _thirsty(**overrides):
    """Une parcelle qui a franchement besoin d'eau, pour exercer les plafonds."""
    base = dict(
        current_depletion_mm=60.0,
        total_available_water_mm=150.0,
        readily_available_water_mm=60.0,
        etc_mm_day=6.0,
        field_area_ha=4.0,
        system=DRIP,
    )
    base.update(overrides)
    return calculate_irrigation_requirement(**base)


def test_a_quota_caps_the_dose_without_changing_the_recommendation():
    """Le quota limite ce qu'on applique, pas ce dont la culture a besoin.

    Les deux chiffres doivent rester distincts à l'écran : « voici ce qu'il
    faudrait » et « voici ce qu'on a le droit d'appliquer » ne se pilotent pas
    de la même façon.
    """
    unlimited = _thirsty()
    limited = _thirsty(seasonal_quota_remaining_m3=1000.0)

    assert unlimited.recommendation is Recommendation.IRRIGATE
    assert limited.recommendation is Recommendation.IRRIGATE
    assert limited.volume_m3 < unlimited.volume_m3
    assert limited.capped_by_quota is True
    assert limited.volume_m3 <= 1000.0 + 1e-6
    assert any("quota" in w.lower() for w in limited.warnings)


def test_a_quota_too_tight_to_be_useful_says_wait_rather_than_dribbling():
    """Un quota qui n'autorise qu'une dose inutile ne doit pas la recommander.

    40 m³ sur 4 ha font 1 mm : sous le seuil d'utilité, un tel apport s'évapore
    avant d'atteindre les racines. Le moteur préfère « à surveiller » à un geste
    qui gaspillerait à la fois l'eau et le tour d'eau. Les deux plafonds — quota
    et dose minimale utile — se composent donc, et c'est voulu.
    """
    result = _thirsty(seasonal_quota_remaining_m3=40.0)
    assert result.capped_by_quota is True
    assert result.net_requirement_mm == 0.0
    assert result.recommendation is Recommendation.MONITOR
    assert any("s'évapore" in w or "utilité" in w for w in result.warnings)


def test_an_exhausted_quota_refuses_rather_than_recommending_zero_silently():
    """Quota épuisé : on le dit, et on ne prétend pas que la parcelle va bien.

    Renvoyer « pas d'irrigation nécessaire » serait faux — la parcelle en a
    besoin, c'est l'autorisation qui manque. La décision remonte à l'humain.
    """
    result = _thirsty(seasonal_quota_remaining_m3=0.0)
    assert result.net_requirement_mm == 0.0
    assert result.capped_by_quota is True
    assert result.recommendation is not Recommendation.NO_IRRIGATION
    assert any("quota" in w.lower() for w in result.warnings)


def test_no_quota_configured_is_not_an_unlimited_quota_nor_a_zero_one():
    """`None` veut dire « aucun quota configuré », et se comporte comme tel."""
    assert _thirsty(seasonal_quota_remaining_m3=None).capped_by_quota is False
    assert _thirsty().capped_by_quota is False


def test_a_generous_quota_does_not_touch_the_dose():
    """Un plafond qui ne mord pas ne doit rien changer, ni au chiffre ni au drapeau."""
    unlimited = _thirsty()
    generous = _thirsty(seasonal_quota_remaining_m3=100_000.0)
    assert generous.volume_m3 == pytest.approx(unlimited.volume_m3)
    assert generous.capped_by_quota is False


def test_every_number_a_user_reads_is_written_in_french():
    """Une virgule décimale, partout, y compris dans les phrases des moteurs.

    Le panneau « Pourquoi cette décision ? » affiche côte à côte les titres
    formatés par l'interface et les phrases produites ici. Mélanger « 4,78 » et
    « 195.0 » dans le même écran oblige le lecteur à décider, chiffre par
    chiffre, si un point est un séparateur décimal ou un séparateur de milliers.
    Sur un coût, cette ambiguïté vaut un facteur mille.
    """
    import re

    result = _requirement(70.0, infiltration_rate_mm_per_hour=3.0)
    # Un point encadré de chiffres est un séparateur décimal anglais.
    english_decimal = re.compile(r"\d\.\d")
    for line in [*result.steps, *result.warnings]:
        assert not english_decimal.search(line), line
    # Et la virgule décimale est bien présente : le test ne passe pas
    # simplement parce qu'aucun nombre n'est affiché.
    assert any(re.search(r"\d,\d", line) for line in result.steps)
