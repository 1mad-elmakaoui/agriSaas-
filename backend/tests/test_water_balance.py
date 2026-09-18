"""Soil water balance and effective rainfall tests."""

import pytest

from app.domain.irrigation.constants import StressLevel
from app.domain.irrigation.water_balance import (
    ForecastDay,
    SoilParameters,
    adjust_depletion_fraction,
    calculate_effective_forecast_rainfall,
    calculate_effective_rainfall,
    calculate_water_balance,
    classify_stress,
    project_depletion,
)

SANDY_LOAM = SoilParameters(
    code="sandy_loam",
    name_fr="Limon sableux",
    field_capacity=0.23,
    wilting_point=0.11,
    infiltration_rate_mm_per_hour=20.0,
)


def _balance(moisture_pct, **kwargs):
    params = dict(
        soil=SANDY_LOAM,
        soil_moisture_pct=moisture_pct,
        root_depth_m=1.0,
        etc_mm_day=5.0,
        depletion_fraction_p=0.50,
    )
    params.update(kwargs)
    return calculate_water_balance(**params)


def test_invalid_soil_profile_rejected():
    with pytest.raises(ValueError):
        SoilParameters(
            code="bad", name_fr="Invalide",
            field_capacity=0.10, wilting_point=0.30,
            infiltration_rate_mm_per_hour=10,
        )


def test_taw_follows_fao56_equation_82():
    # TAW = 1000 * (0.23 - 0.11) * 1.0 m = 120 mm
    assert _balance(23.0).total_available_water_mm == pytest.approx(120.0)


def test_raw_is_p_times_taw():
    result = _balance(23.0)
    assert result.readily_available_water_mm == pytest.approx(
        result.depletion_fraction_p_adjusted * result.total_available_water_mm
    )


def test_at_field_capacity_there_is_no_deficit_and_no_stress():
    result = _balance(23.0)
    assert result.depletion_mm == pytest.approx(0.0)
    assert result.water_stress_coefficient == pytest.approx(1.0)
    assert result.stress_level is StressLevel.NORMAL


def test_at_wilting_point_the_reservoir_is_empty():
    result = _balance(11.0)
    assert result.depletion_mm == pytest.approx(result.total_available_water_mm)
    assert result.available_water_mm == pytest.approx(0.0)
    assert result.water_stress_coefficient == pytest.approx(0.0)
    assert result.stress_level is StressLevel.CRITICAL


def test_ks_stays_at_one_within_readily_available_water():
    # p_adj = 0.50 -> RAW = 60 mm. A 30 mm deficit is well inside RAW.
    result = _balance(20.0)  # deficit = 1000*(0.23-0.20)*1 = 30 mm
    assert result.depletion_mm == pytest.approx(30.0)
    assert result.water_stress_coefficient == pytest.approx(1.0)


def test_ks_decreases_linearly_beyond_raw():
    result = _balance(14.0)  # deficit = 90 mm, TAW 120, RAW ~60
    expected = (result.total_available_water_mm - result.depletion_mm) / (
        result.total_available_water_mm - result.readily_available_water_mm
    )
    assert result.water_stress_coefficient == pytest.approx(expected)
    assert result.water_stress_coefficient < 1.0


def test_drier_soil_means_more_stress():
    levels = [_balance(m).stress_level for m in (23.0, 15.0, 13.0, 11.0)]
    severity = {
        StressLevel.NORMAL: 0,
        StressLevel.MODERATE: 1,
        StressLevel.HIGH: 2,
        StressLevel.CRITICAL: 3,
    }
    assert [severity[level] for level in levels] == sorted(severity[level] for level in levels)


def test_actual_etc_is_reduced_by_stress():
    stressed = _balance(13.0)
    assert stressed.actual_etc_mm_day < 5.0
    assert stressed.actual_etc_mm_day == pytest.approx(stressed.water_stress_coefficient * 5.0)


def test_moisture_above_field_capacity_is_warned_not_silently_used():
    result = _balance(30.0)
    assert result.depletion_mm == pytest.approx(0.0)
    assert any("capacité au champ" in w for w in result.warnings)


def test_moisture_below_wilting_point_is_warned():
    result = _balance(5.0)
    assert any("flétrissement" in w for w in result.warnings)


def test_rainfall_reduces_the_deficit():
    dry = _balance(17.0)
    wet = _balance(17.0, effective_rainfall_mm=20.0)
    assert wet.depletion_mm == pytest.approx(dry.depletion_mm - 20.0)


def test_excess_water_is_reported_as_deep_percolation():
    result = _balance(22.0, effective_rainfall_mm=50.0)
    assert result.depletion_mm == pytest.approx(0.0)
    assert any("percolation" in w for w in result.warnings)


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        _balance(17.0, root_depth_m=0.0)
    with pytest.raises(ValueError):
        _balance(150.0)


def test_depletion_fraction_adjusted_for_evaporative_demand():
    # FAO-56 eq. 84: p_adj = p + 0.04 * (5 - ETc)
    high_demand, _ = adjust_depletion_fraction(0.50, 8.0)
    low_demand, _ = adjust_depletion_fraction(0.50, 2.0)
    assert high_demand < 0.50 < low_demand
    assert high_demand == pytest.approx(0.50 + 0.04 * (5 - 8))


def test_depletion_fraction_is_clipped_to_configured_bounds():
    assert adjust_depletion_fraction(0.20, 20.0)[0] >= 0.10
    assert adjust_depletion_fraction(0.75, 0.0)[0] <= 0.80


def test_stress_classification_boundaries():
    assert classify_stress(1.0) is StressLevel.NORMAL
    assert classify_stress(0.75) is StressLevel.MODERATE
    assert classify_stress(0.40) is StressLevel.HIGH
    assert classify_stress(0.10) is StressLevel.CRITICAL


# ------------------------------------------------------------------ rainfall
def test_small_rain_is_not_effective():
    result = calculate_effective_rainfall(1.5)
    assert result.effective_mm == 0.0
    assert "seuil d'interception" in result.notes[0]


def test_effective_rainfall_applies_threshold_and_infiltration_fraction():
    # (10 - 2) * 0.80 = 6.4 mm
    assert calculate_effective_rainfall(10.0).effective_mm == pytest.approx(6.4)


def test_forecast_below_probability_threshold_is_ignored():
    result = calculate_effective_forecast_rainfall([ForecastDay(1, 20.0, 0.20)])
    assert result.effective_mm == 0.0
    assert result.gross_mm == 20.0
    assert "non prise en compte" in result.per_day[0]["reason_fr"]


def test_forecast_is_never_subtracted_at_face_value():
    result = calculate_effective_forecast_rainfall([ForecastDay(1, 20.0, 0.90)])
    assert 0 < result.effective_mm < 20.0


def test_forecast_beyond_horizon_is_ignored():
    result = calculate_effective_forecast_rainfall([ForecastDay(10, 30.0, 0.95)])
    assert result.effective_mm == 0.0


def test_higher_probability_yields_more_counted_rain():
    low = calculate_effective_forecast_rainfall([ForecastDay(1, 20.0, 0.55)]).effective_mm
    high = calculate_effective_forecast_rainfall([ForecastDay(1, 20.0, 0.95)]).effective_mm
    assert high > low


# ---------------------------------------------------------------- projection
def test_projection_dries_the_soil_over_time():
    trajectory = project_depletion(
        initial_depletion_mm=20.0, taw_mm=120.0, raw_mm=60.0, etc_mm_day=5.0, days=5
    )
    depletions = [d["depletion_mm"] for d in trajectory]
    assert depletions == sorted(depletions)
    assert len(trajectory) == 5


def test_projection_never_exceeds_total_available_water():
    trajectory = project_depletion(
        initial_depletion_mm=100.0, taw_mm=120.0, raw_mm=60.0, etc_mm_day=10.0, days=30
    )
    assert max(d["depletion_mm"] for d in trajectory) <= 120.0


def test_irrigation_refills_the_reservoir():
    without = project_depletion(
        initial_depletion_mm=80.0, taw_mm=120.0, raw_mm=60.0, etc_mm_day=5.0, days=3
    )
    with_water = project_depletion(
        initial_depletion_mm=80.0, taw_mm=120.0, raw_mm=60.0, etc_mm_day=5.0, days=3,
        irrigation_mm=50.0,
    )
    assert with_water[-1]["depletion_mm"] < without[-1]["depletion_mm"]


def test_stressed_crop_transpires_less():
    trajectory = project_depletion(
        initial_depletion_mm=110.0, taw_mm=120.0, raw_mm=60.0, etc_mm_day=6.0, days=3
    )
    assert all(d["actual_etc_mm"] < 6.0 for d in trajectory)
