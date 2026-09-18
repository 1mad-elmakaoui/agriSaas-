"""ET0 unit tests, anchored on the FAO-56 worked examples."""

from datetime import date

import pytest

from app.domain.irrigation.constants import ET0Method
from app.domain.irrigation.et0 import (
    atmospheric_pressure,
    calculate_et0,
    extraterrestrial_radiation,
    psychrometric_constant,
    saturation_vapour_pressure,
    slope_of_vapour_pressure_curve,
    wind_speed_at_2m,
)


def test_saturation_vapour_pressure_matches_fao56_example_3():
    # FAO-56 Example 3: Tmax 24.5 degC -> 3.075 kPa, Tmin 15 degC -> 1.705 kPa
    assert saturation_vapour_pressure(24.5) == pytest.approx(3.075, abs=0.002)
    assert saturation_vapour_pressure(15.0) == pytest.approx(1.705, abs=0.002)


def test_slope_matches_fao56_example_5():
    # FAO-56 Example 5: T = 30 degC -> Delta = 0.243 kPa/degC
    assert slope_of_vapour_pressure_curve(30.0) == pytest.approx(0.243, abs=0.002)


def test_atmospheric_pressure_and_psychrometric_constant_example_2():
    # FAO-56 Example 2: z = 1800 m -> P = 81.8 kPa, gamma = 0.054 kPa/degC
    assert atmospheric_pressure(1800) == pytest.approx(81.8, abs=0.1)
    assert psychrometric_constant(1800) == pytest.approx(0.054, abs=0.001)


def test_extraterrestrial_radiation_example_8():
    # FAO-56 Example 8: 3 September, latitude 20 degS -> Ra = 32.2 MJ/m2/day
    doy = date(2024, 9, 3).timetuple().tm_yday
    assert extraterrestrial_radiation(-20.0, doy) == pytest.approx(32.2, abs=0.2)


def test_wind_speed_conversion_example_14():
    # FAO-56 Example 14: 3.2 m/s measured at 10 m -> 2.4 m/s at 2 m
    assert wind_speed_at_2m(3.2, 10.0) == pytest.approx(2.4, abs=0.05)


def test_penman_monteith_matches_fao56_example_18():
    """FAO-56 Example 18 (Brussels, 6 July): ET0 = 3.9 mm/day."""
    result = calculate_et0(
        temp_max_c=21.5,
        temp_min_c=12.3,
        latitude_deg=50.80,
        day=date(2024, 7, 6),
        relative_humidity_max_pct=84,
        relative_humidity_min_pct=63,
        wind_speed_m_s=2.078,
        wind_measurement_height_m=2.0,
        solar_radiation_mj_m2_day=22.07,
        elevation_m=100,
    )
    assert result.method is ET0Method.PENMAN_MONTEITH
    assert result.et0_mm_day == pytest.approx(3.9, abs=0.05)
    assert result.intermediates["net_radiation_rn"] == pytest.approx(13.28, abs=0.05)
    assert result.intermediates["vapour_pressure_deficit"] == pytest.approx(0.589, abs=0.005)


def test_falls_back_to_hargreaves_when_humidity_and_wind_missing():
    result = calculate_et0(
        temp_max_c=34.0,
        temp_min_c=19.0,
        latitude_deg=30.42,
        day=date(2026, 7, 15),
    )
    assert result.method is ET0Method.HARGREAVES
    assert result.et0_mm_day > 0
    # The user must be told the fallback was used.
    assert any("repli" in w.lower() for w in result.warnings)


def test_hargreaves_matches_fao56_example_20():
    """FAO-56 Example 20: Lyon, July, Tmax 26.6, Tmin 14.8 -> ET0 = 5.0 mm/day."""
    result = calculate_et0(
        temp_max_c=26.6,
        temp_min_c=14.8,
        latitude_deg=45.72,
        day=date(2024, 7, 15),
    )
    assert result.et0_mm_day == pytest.approx(5.0, abs=0.2)


def test_solar_radiation_estimated_when_absent_is_flagged():
    result = calculate_et0(
        temp_max_c=30.0,
        temp_min_c=15.0,
        latitude_deg=31.6,
        day=date(2026, 6, 1),
        relative_humidity_mean_pct=45,
        wind_speed_m_s=2.5,
    )
    assert result.method is ET0Method.PENMAN_MONTEITH
    assert any("Rayonnement solaire" in w for w in result.warnings)


def test_missing_temperature_raises_rather_than_inventing():
    with pytest.raises(ValueError):
        calculate_et0(
            temp_max_c=None, temp_min_c=15.0, latitude_deg=31.0, day=date(2026, 6, 1)
        )


def test_inverted_temperatures_rejected():
    with pytest.raises(ValueError):
        calculate_et0(
            temp_max_c=10.0, temp_min_c=25.0, latitude_deg=31.0, day=date(2026, 6, 1)
        )


def test_et0_is_deterministic():
    kwargs = dict(
        temp_max_c=32.0,
        temp_min_c=18.0,
        latitude_deg=31.63,
        day=date(2026, 8, 22),
        relative_humidity_mean_pct=50,
        wind_speed_m_s=2.0,
        elevation_m=450,
    )
    assert calculate_et0(**kwargs).et0_mm_day == calculate_et0(**kwargs).et0_mm_day


def test_hotter_drier_windier_means_more_et0():
    base = dict(latitude_deg=31.6, day=date(2026, 7, 1), elevation_m=100)
    mild = calculate_et0(
        temp_max_c=25, temp_min_c=15, relative_humidity_mean_pct=70,
        wind_speed_m_s=1.0, **base,
    )
    harsh = calculate_et0(
        temp_max_c=40, temp_min_c=22, relative_humidity_mean_pct=20,
        wind_speed_m_s=6.0, **base,
    )
    assert harsh.et0_mm_day > mild.et0_mm_day


def test_a_zero_humidity_reading_is_used_not_treated_as_missing():
    """0 % d'humidité est une mesure, pas une absence.

    Le code portait `relative_humidity_max_pct or relative_humidity_min_pct` :
    en Python, `0.0` est faux, donc une humidité maximale nulle basculait
    silencieusement sur la minimale — et, si les deux étaient nulles, faisait
    tomber le calcul sur Hargreaves-Samani sans que rien ne le justifie.

    Une humidité relative nulle est rare mais physiquement possible en
    conditions désertiques, et c'est précisément le régime où le Souss et les
    plateaux de l'Est comptent. Le test fige la comparaison à `None`.
    """
    result = calculate_et0(
        temp_max_c=42.0,
        temp_min_c=24.0,
        latitude_deg=30.4,
        day=date(2026, 7, 15),
        relative_humidity_max_pct=0.0,
        relative_humidity_min_pct=0.0,
        wind_speed_m_s=3.0,
        elevation_m=50.0,
    )
    # Penman-Monteith a bien tourné : l'humidité n'a pas été jugée absente.
    assert result.method is ET0Method.PENMAN_MONTEITH
    assert not any("Hargreaves" in w for w in result.warnings)
    # Et l'air parfaitement sec produit une demande évaporative élevée.
    assert result.et0_mm_day > 8.0


def test_a_zero_mean_humidity_is_also_honoured():
    """Même règle par le chemin de l'humidité moyenne (FAO-56 éq. 19)."""
    result = calculate_et0(
        temp_max_c=40.0,
        temp_min_c=22.0,
        latitude_deg=31.5,
        day=date(2026, 7, 15),
        relative_humidity_mean_pct=0.0,
        wind_speed_m_s=2.5,
        elevation_m=460.0,
    )
    assert result.method is ET0Method.PENMAN_MONTEITH


def test_a_measured_radiation_input_produces_no_warning():
    """Une entrée mesurée n'est pas une dégradation.

    La section « Avertissements » du panneau doit rester rare pour rester lue.
    Une ligne « Rayonnement solaire : mesurée/API » à chaque calcul apprend au
    lecteur à la survoler, et c'est alors la ligne suivante — celle qui compte —
    qu'il ne verra pas.
    """
    result = calculate_et0(
        temp_max_c=30.0,
        temp_min_c=15.0,
        latitude_deg=31.6,
        day=date(2026, 6, 1),
        relative_humidity_mean_pct=45,
        wind_speed_m_s=2.5,
        solar_radiation_mj_m2_day=24.0,
    )
    assert not any("Rayonnement solaire" in w for w in result.warnings)


def test_an_estimated_radiation_is_disclosed_exactly_once():
    """La dégradation est annoncée, et une seule fois."""
    result = calculate_et0(
        temp_max_c=30.0,
        temp_min_c=15.0,
        latitude_deg=31.6,
        day=date(2026, 6, 1),
        relative_humidity_mean_pct=45,
        wind_speed_m_s=2.5,
    )
    disclosures = [w for w in result.warnings if "Rayonnement solaire" in w]
    assert len(disclosures) == 1
    assert "éq. 50" in disclosures[0]
