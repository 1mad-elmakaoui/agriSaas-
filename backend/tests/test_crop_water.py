"""ETc and growth-stage tests."""

from datetime import date, timedelta

import pytest

from app.domain.enums import StageSource
from app.domain.irrigation.crop_water import (
    CropParameters,
    GrowthStage,
    GrowthStageInfo,
    calculate_crop_evapotranspiration,
    crop_coefficient,
    estimate_growth_stage,
    root_depth_for_stage,
)

TOMATO = CropParameters(
    code="tomato",
    name_fr="Tomate",
    kc_initial=0.60,
    kc_mid=1.15,
    kc_end=0.80,
    stage_lengths_days={"INITIAL": 30, "DEVELOPMENT": 40, "MID_SEASON": 40, "LATE_SEASON": 25},
    root_depth_min_m=0.7,
    root_depth_max_m=1.5,
    depletion_fraction_p=0.40,
)

OLIVE = CropParameters(
    code="olive",
    name_fr="Olivier",
    kc_initial=0.65,
    kc_mid=0.70,
    kc_end=0.70,
    stage_lengths_days={"INITIAL": 30, "DEVELOPMENT": 90, "MID_SEASON": 60, "LATE_SEASON": 90},
    root_depth_min_m=1.2,
    root_depth_max_m=1.7,
    depletion_fraction_p=0.65,
    perennial=True,
    cycle_start_month=3,
)


def _stage_at(crop, days, planting=date(2026, 3, 1)):
    return estimate_growth_stage(
        crop, on_date=planting + timedelta(days=days), planting_date=planting
    )


def test_etc_is_et0_times_kc():
    stage = GrowthStageInfo(stage=GrowthStage.MID_SEASON, source=StageSource.DECLARED)
    result = calculate_crop_evapotranspiration(et0_mm_day=5.8, crop=TOMATO, stage_info=stage)
    assert result.kc == pytest.approx(1.15)
    assert result.etc_mm_day == pytest.approx(5.8 * 1.15)


def test_growth_stage_progression_from_planting_date():
    assert _stage_at(TOMATO, 5).stage is GrowthStage.INITIAL
    assert _stage_at(TOMATO, 45).stage is GrowthStage.DEVELOPMENT
    assert _stage_at(TOMATO, 85).stage is GrowthStage.MID_SEASON
    assert _stage_at(TOMATO, 120).stage is GrowthStage.LATE_SEASON


def test_estimated_stage_is_always_labelled_as_an_estimate():
    stage = _stage_at(TOMATO, 45)
    assert stage.source is StageSource.ESTIMATED
    assert stage.is_estimate is True
    assert stage.note_fr


def test_kc_is_constant_during_initial_and_mid_season():
    assert crop_coefficient(TOMATO, _stage_at(TOMATO, 2))[0] == pytest.approx(0.60)
    assert crop_coefficient(TOMATO, _stage_at(TOMATO, 25))[0] == pytest.approx(0.60)
    assert crop_coefficient(TOMATO, _stage_at(TOMATO, 75))[0] == pytest.approx(1.15)


def test_kc_interpolates_linearly_during_development():
    # Halfway through development: Kc = (0.60 + 1.15) / 2
    stage = _stage_at(TOMATO, 50)  # 20 days into a 40-day development stage
    kc, explanation = crop_coefficient(TOMATO, stage)
    assert kc == pytest.approx((0.60 + 1.15) / 2, abs=0.01)
    assert "interpolé" in explanation


def test_kc_decreases_during_late_season():
    early_late = crop_coefficient(TOMATO, _stage_at(TOMATO, 112))[0]
    end_late = crop_coefficient(TOMATO, _stage_at(TOMATO, 132))[0]
    assert TOMATO.kc_mid > early_late > end_late >= TOMATO.kc_end


def test_root_depth_grows_then_plateaus():
    initial = root_depth_for_stage(TOMATO, _stage_at(TOMATO, 5))[0]
    mid_dev = root_depth_for_stage(TOMATO, _stage_at(TOMATO, 50))[0]
    mid_season = root_depth_for_stage(TOMATO, _stage_at(TOMATO, 85))[0]
    assert initial == pytest.approx(0.7)
    assert 0.7 < mid_dev < 1.5
    assert mid_season == pytest.approx(1.5)


def test_perennial_uses_annual_cycle_and_max_root_depth():
    stage = estimate_growth_stage(OLIVE, on_date=date(2026, 8, 22))
    assert stage.source is StageSource.ESTIMATED
    assert "pérenne" in stage.note_fr
    assert root_depth_for_stage(OLIVE, stage)[0] == pytest.approx(1.7)


def test_perennial_cycle_wraps_around_the_year():
    a = estimate_growth_stage(OLIVE, on_date=date(2026, 5, 1))
    b = estimate_growth_stage(OLIVE, on_date=date(2027, 5, 1))
    assert a.stage is b.stage


def test_annual_crop_without_planting_date_refuses_to_guess():
    with pytest.raises(ValueError):
        estimate_growth_stage(TOMATO, on_date=date(2026, 6, 1))


def test_planting_date_in_the_future_is_rejected():
    with pytest.raises(ValueError):
        estimate_growth_stage(TOMATO, on_date=date(2026, 1, 1), planting_date=date(2026, 6, 1))


def test_negative_et0_rejected():
    stage = GrowthStageInfo(stage=GrowthStage.MID_SEASON, source=StageSource.DECLARED)
    with pytest.raises(ValueError):
        calculate_crop_evapotranspiration(et0_mm_day=-1.0, crop=TOMATO, stage_info=stage)


def test_etc_declares_that_the_climate_adjustment_is_not_applied():
    """Une limite connue voyage avec le chiffre, pas dans un document à part.

    Les Kc tabulés supposent un climat sub-humide ; l'intérieur marocain n'en
    est pas un. Sans la correction de l'équation 62, l'ETc est sous-estimée —
    donc la dose aussi — et une sous-irrigation ne se voit pas : la parcelle a
    l'air normale et le rendement baisse.

    Le test verrouille deux choses : que la limite est énoncée, et qu'elle
    énonce le **sens** du biais. « Précision réduite » n'aiderait personne à
    décider.
    """
    stage = GrowthStageInfo(stage=GrowthStage.MID_SEASON, source=StageSource.DECLARED)
    result = calculate_crop_evapotranspiration(et0_mm_day=6.0, crop=TOMATO, stage_info=stage)

    assert result.caveats_fr, "ETc carries no caveat at all"
    caveat = " ".join(result.caveats_fr)
    assert "62" in caveat, "the caveat does not name the FAO equation it refers to"
    assert "supérieur" in caveat, "the caveat does not state which way the bias runs"
    assert "caveats_fr" in result.to_dict()
