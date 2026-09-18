"""Soil water balance of the root zone.

The model is the FAO-56 root-zone depletion balance (chapter 8):

    TAW = 1000 x (theta_FC - theta_WP) x Zr        (eq. 82)
    RAW = p x TAW                                   (eq. 83)
    Dr  = 1000 x (theta_FC - theta) x Zr            root-zone depletion, mm
    Ks  = (TAW - Dr) / (TAW - RAW)   for Dr > RAW   (eq. 84)
    Ks  = 1                          for Dr <= RAW

and the daily update (eq. 85):

    Dr(i) = Dr(i-1) - P_eff - I_net + ETc_adj + DP

where ETc_adj = Ks x ETc is the *actual* crop evapotranspiration under stress,
and DP (deep percolation) is the water that would push Dr below 0.

Everything is expressed in millimetres of water over the root zone, which is
what makes the numbers comparable to rainfall and to irrigation depth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.formatting import fr, fr_pct
from app.domain.irrigation.constants import (
    MODEL_CONFIG,
    STRESS_LEVEL_FR,
    RainfallConfig,
    StressLevel,
    WaterBalanceConfig,
)

__all__ = [
    "EffectiveRainfall",
    "ForecastDay",
    "SoilParameters",
    "WaterBalanceResult",
    "adjust_depletion_fraction",
    "calculate_effective_forecast_rainfall",
    "calculate_effective_rainfall",
    "calculate_water_balance",
    "classify_stress",
    "project_depletion",
]


@dataclass(frozen=True)
class SoilParameters:
    """Hydraulic properties of the field's soil profile.

    ``field_capacity`` and ``wilting_point`` are volumetric water contents
    (m3/m3), i.e. 0.23 means 23 % by volume.
    """

    code: str
    name_fr: str
    field_capacity: float
    wilting_point: float
    #: `None` quand le profil de sol n'en documente aucun. Le plafond
    #: anti-ruissellement ne peut alors pas être appliqué, et la
    #: recommandation doit le dire plutôt que de laisser croire qu'il l'a été.
    infiltration_rate_mm_per_hour: float | None = None

    def __post_init__(self) -> None:
        if not 0 < self.wilting_point < self.field_capacity < 1:
            raise ValueError(
                "Profil de sol invalide : il faut 0 < point de flétrissement "
                "< capacité au champ < 1."
            )

    @property
    def available_water_mm_per_m(self) -> float:
        return 1000.0 * (self.field_capacity - self.wilting_point)


@dataclass
class ForecastDay:
    """One day of rain forecast."""

    day_offset: int  # 0 = today
    precipitation_mm: float
    probability: float | None = None  # 0..1


@dataclass
class EffectiveRainfall:
    """Rainfall that actually reaches the root zone."""

    effective_mm: float
    gross_mm: float
    method_fr: str
    per_day: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "effective_mm": round(self.effective_mm, 2),
            "gross_mm": round(self.gross_mm, 2),
            "method_fr": self.method_fr,
            "per_day": self.per_day,
            "notes": self.notes,
        }


def calculate_effective_rainfall(
    precipitation_mm: float,
    config: RainfallConfig | None = None,
) -> EffectiveRainfall:
    """Convert observed rainfall into effective rainfall.

    Small events are lost to canopy interception and evaporation; the rest is
    reduced by a runoff/interception factor. Both parameters are configurable
    (see ``RainfallConfig``) and reported back to the user.
    """
    cfg = config or MODEL_CONFIG.rainfall
    gross = max(0.0, precipitation_mm)

    if gross < cfg.interception_threshold_mm:
        effective = 0.0
        note = (
            f"Pluie de {fr(gross, 1)} mm inférieure au seuil d'interception "
            f"({cfg.interception_threshold_mm} mm) : considérée comme non efficace "
            "(interceptée par le feuillage puis évaporée)."
        )
    else:
        effective = (gross - cfg.interception_threshold_mm) * cfg.infiltration_fraction
        note = (
            f"Pluie efficace = ({fr(gross, 1)} − {cfg.interception_threshold_mm}) × "
            f"{fr_pct(cfg.infiltration_fraction, 0)} = {fr(effective, 1)} mm."
        )

    return EffectiveRainfall(
        effective_mm=effective,
        gross_mm=gross,
        method_fr=(
            "Seuil d'interception + coefficient d'infiltration (analogue journalier "
            "simplifié de la méthode USDA-SCS, qui est définie au pas mensuel)."
        ),
        notes=[note],
    )


def calculate_effective_forecast_rainfall(
    forecast: list[ForecastDay],
    config: RainfallConfig | None = None,
) -> EffectiveRainfall:
    """Convert a rain FORECAST into the water we are willing to count on.

    A forecast is not a measurement. Rather than subtracting every forecast
    millimetre from the irrigation requirement — which would leave the crop dry
    whenever the rain fails to materialise — we:

    1. keep only the days inside the planning horizon;
    2. ignore forecasts whose probability is below ``min_probability``;
    3. weight the amount by its own probability;
    4. apply a global reliability discount;
    5. apply the same interception/infiltration rule as for observed rain.
    """
    cfg = config or MODEL_CONFIG.rainfall
    total_effective = 0.0
    total_gross = 0.0
    per_day: list[dict[str, Any]] = []
    notes: list[str] = []

    for day in sorted(forecast, key=lambda d: d.day_offset):
        if day.day_offset < 0 or day.day_offset >= cfg.forecast_horizon_days:
            continue
        gross = max(0.0, day.precipitation_mm)
        total_gross += gross
        probability = day.probability

        if probability is None:
            # No probability supplied: we do not assume 100 %. The forecast
            # amount is kept but flagged, and the reliability discount applies.
            probability_used = 1.0
            note = (
                "probabilité non fournie par l'API : montant non pondéré, "
                "remise de fiabilité appliquée"
            )
        elif probability < cfg.min_probability:
            per_day.append(
                {
                    "day_offset": day.day_offset,
                    "precipitation_mm": round(gross, 2),
                    "probability": round(probability, 2),
                    "counted_mm": 0.0,
                    "reason_fr": (
                        f"Probabilité {fr_pct(probability, 0)} inférieure au seuil "
                        f"{fr_pct(cfg.min_probability, 0)} : pluie non prise en compte."
                    ),
                }
            )
            continue
        else:
            probability_used = probability
            note = f"pondérée par la probabilité {fr_pct(probability, 0)}"

        weighted = gross * probability_used * cfg.reliability_factor
        effective = calculate_effective_rainfall(weighted, cfg).effective_mm
        total_effective += effective

        per_day.append(
            {
                "day_offset": day.day_offset,
                "precipitation_mm": round(gross, 2),
                "probability": round(probability, 2) if probability is not None else None,
                "counted_mm": round(effective, 2),
                "reason_fr": (
                    f"{fr(gross, 1)} mm {note}, remise de fiabilité "
                    f"{fr_pct(cfg.reliability_factor, 0)}, puis seuil d'interception → "
                    f"{fr(effective, 1)} mm retenus."
                ),
            }
        )

    if total_gross > 0 and total_effective == 0:
        notes.append(
            "Des pluies sont annoncées mais aucune n'est retenue dans le calcul "
            "(probabilité trop faible ou quantité trop faible pour atteindre les racines)."
        )

    return EffectiveRainfall(
        effective_mm=total_effective,
        gross_mm=total_gross,
        method_fr=(
            f"Prévisions sur {cfg.forecast_horizon_days} jours, seuil de probabilité "
            f"{fr_pct(cfg.min_probability, 0)}, pondération par la probabilité, remise de "
            f"fiabilité {fr_pct(cfg.reliability_factor, 0)}. Les millimètres annoncés ne sont "
            "jamais soustraits tels quels du besoin en irrigation."
        ),
        per_day=per_day,
        notes=notes,
    )


def adjust_depletion_fraction(
    p: float,
    etc_mm_day: float,
    config: WaterBalanceConfig | None = None,
) -> tuple[float, str]:
    """Adjust the tabulated depletion fraction for the actual ETc.

    FAO-56 eq. 84 note: p_adj = p + 0.04 x (5 - ETc), clipped to [0.1, 0.8].
    Crops tolerate less depletion when evaporative demand is high.
    """
    cfg = config or MODEL_CONFIG.water_balance
    if not cfg.adjust_p_for_etc:
        return p, "Fraction d'épuisement tabulée utilisée sans ajustement."
    adjusted = p + 0.04 * (5.0 - etc_mm_day)
    adjusted = max(cfg.p_min, min(cfg.p_max, adjusted))
    return adjusted, (
        f"Fraction d'épuisement ajustée à la demande évaporative : "
        f"p = {fr(p, 2)} + 0,04 × (5 − {fr(etc_mm_day, 2)}) = {fr(adjusted, 2)} (FAO-56 éq. 84)."
    )


def classify_stress(ks: float, config: WaterBalanceConfig | None = None) -> StressLevel:
    """Map the FAO-56 water-stress coefficient Ks to a named stress class.

    The class boundaries are a presentation choice and live in
    ``WaterBalanceConfig`` so they can be tuned without touching this logic.
    """
    cfg = config or MODEL_CONFIG.water_balance
    if ks >= cfg.ks_normal_threshold:
        return StressLevel.NORMAL
    if ks >= cfg.ks_moderate_threshold:
        return StressLevel.MODERATE
    if ks >= cfg.ks_high_threshold:
        return StressLevel.HIGH
    return StressLevel.CRITICAL


@dataclass
class WaterBalanceResult:
    """State of the root-zone water reservoir."""

    total_available_water_mm: float  # TAW
    readily_available_water_mm: float  # RAW
    available_water_mm: float  # water still usable by the crop
    depletion_mm: float  # Dr
    depletion_fraction: float  # Dr / TAW
    water_stress_coefficient: float  # Ks
    stress_level: StressLevel
    soil_moisture_pct: float
    root_depth_m: float
    depletion_fraction_p_adjusted: float
    actual_etc_mm_day: float  # ETc_adj = Ks x ETc
    optimal_moisture_min_pct: float
    optimal_moisture_max_pct: float
    explanations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def stress_label_fr(self) -> str:
        return STRESS_LEVEL_FR[self.stress_level]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_available_water_mm": round(self.total_available_water_mm, 2),
            "readily_available_water_mm": round(self.readily_available_water_mm, 2),
            "available_water_mm": round(self.available_water_mm, 2),
            "water_deficit_mm": round(self.depletion_mm, 2),
            "depletion_fraction": round(self.depletion_fraction, 3),
            "water_stress_coefficient_ks": round(self.water_stress_coefficient, 3),
            "stress_level": self.stress_level.value,
            "stress_label_fr": self.stress_label_fr,
            "soil_moisture_pct": round(self.soil_moisture_pct, 2),
            "root_depth_m": round(self.root_depth_m, 2),
            "depletion_fraction_p_adjusted": round(self.depletion_fraction_p_adjusted, 3),
            "actual_etc_mm_day": round(self.actual_etc_mm_day, 3),
            "optimal_moisture_range_pct": [
                round(self.optimal_moisture_min_pct, 1),
                round(self.optimal_moisture_max_pct, 1),
            ],
            "explanations": self.explanations,
            "warnings": self.warnings,
        }


def calculate_water_balance(
    *,
    soil: SoilParameters,
    soil_moisture_pct: float,
    root_depth_m: float,
    etc_mm_day: float,
    depletion_fraction_p: float,
    effective_rainfall_mm: float = 0.0,
    irrigation_mm: float = 0.0,
    config: WaterBalanceConfig | None = None,
) -> WaterBalanceResult:
    """Compute the root-zone water balance for the current day.

    Args:
        soil_moisture_pct: volumetric water content in percent (27 = 27 % v/v).
        root_depth_m: effective rooting depth Zr.
        etc_mm_day: potential crop evapotranspiration for the day.
        effective_rainfall_mm / irrigation_mm: water added since the moisture
            measurement, if any.

    Raises:
        ValueError: on physically impossible inputs. Nothing is silently fixed.
    """
    cfg = config or MODEL_CONFIG.water_balance

    if root_depth_m <= 0:
        raise ValueError("La profondeur racinaire doit être strictement positive.")
    if not 0.0 <= soil_moisture_pct <= 100.0:
        raise ValueError("L'humidité du sol doit être exprimée en pourcentage entre 0 et 100.")

    warnings: list[str] = []
    explanations: list[str] = []

    theta = soil_moisture_pct / 100.0
    theta_fc = soil.field_capacity
    theta_wp = soil.wilting_point

    if theta > theta_fc:
        warnings.append(
            f"L'humidité mesurée ({fr(soil_moisture_pct, 1)} %) dépasse la capacité au champ "
            f"du sol {soil.name_fr} ({fr(theta_fc * 100, 1)} %). L'excédent est considéré "
            "comme drainé en profondeur (percolation) et n'est pas comptabilisé comme "
            "réserve utilisable."
        )
    if theta < theta_wp:
        warnings.append(
            f"L'humidité mesurée ({fr(soil_moisture_pct, 1)} %) est inférieure au point de "
            f"flétrissement du sol {soil.name_fr} ({fr(theta_wp * 100, 1)} %). La culture "
            "est en situation de stress sévère."
        )

    # TAW / RAW (FAO-56 eq. 82, 83)
    taw = 1000.0 * (theta_fc - theta_wp) * root_depth_m
    p_adj, p_explanation = adjust_depletion_fraction(depletion_fraction_p, etc_mm_day, cfg)
    raw = p_adj * taw

    explanations.append(
        f"Réserve utile totale (RU) = 1000 × ({fr(theta_fc, 3)} − {fr(theta_wp, 3)}) × "
        f"{fr(root_depth_m, 2)} m = {fr(taw, 1)} mm."
    )
    explanations.append(p_explanation)
    explanations.append(
        f"Réserve facilement utilisable (RFU) = {fr(p_adj, 2)} × {fr(taw, 1)} = {fr(raw, 1)} mm."
    )

    # Current depletion, before adding water
    depletion = 1000.0 * (theta_fc - theta) * root_depth_m
    depletion = max(0.0, min(taw, depletion))
    explanations.append(
        f"Déficit hydrique initial = 1000 × ({fr(theta_fc, 3)} − {fr(theta, 3)}) × "
        f"{fr(root_depth_m, 2)} = {fr(depletion, 1)} mm."
    )

    # Water added since the measurement (FAO-56 eq. 85)
    water_added = effective_rainfall_mm + irrigation_mm
    if water_added > 0:
        before = depletion
        depletion = max(0.0, depletion - water_added)
        percolation = max(0.0, water_added - before)
        explanations.append(
            f"Apports pris en compte : pluie efficace {fr(effective_rainfall_mm, 1)} mm + "
            f"irrigation {fr(irrigation_mm, 1)} mm → déficit ramené à {fr(depletion, 1)} mm."
        )
        if percolation > 0.1:
            warnings.append(
                f"{fr(percolation, 1)} mm d'apport dépassent la capacité de stockage du sol "
                "et sont perdus par percolation profonde."
            )

    available = max(0.0, taw - depletion)
    depletion_fraction = depletion / taw if taw > 0 else 0.0

    # Water stress coefficient (FAO-56 eq. 84)
    if depletion <= raw:
        ks = 1.0
        explanations.append(
            f"Déficit ({fr(depletion, 1)} mm) inférieur à la RFU ({fr(raw, 1)} mm) : "
            "pas de stress hydrique, Ks = 1."
        )
    elif taw > raw:
        ks = max(0.0, (taw - depletion) / (taw - raw))
        explanations.append(
            f"Coefficient de stress Ks = (RU − déficit) / (RU − RFU) = "
            f"({fr(taw, 1)} − {fr(depletion, 1)}) / ({fr(taw, 1)} − {fr(raw, 1)}) = {fr(ks, 2)}."
        )
    else:
        ks = 1.0

    stress = classify_stress(ks, cfg)

    optimal_min = (theta_fc - p_adj * (theta_fc - theta_wp)) * 100.0
    optimal_max = theta_fc * 100.0

    return WaterBalanceResult(
        total_available_water_mm=taw,
        readily_available_water_mm=raw,
        available_water_mm=available,
        depletion_mm=depletion,
        depletion_fraction=depletion_fraction,
        water_stress_coefficient=ks,
        stress_level=stress,
        soil_moisture_pct=soil_moisture_pct,
        root_depth_m=root_depth_m,
        depletion_fraction_p_adjusted=p_adj,
        actual_etc_mm_day=ks * etc_mm_day,
        optimal_moisture_min_pct=optimal_min,
        optimal_moisture_max_pct=optimal_max,
        explanations=explanations,
        warnings=warnings,
    )


def project_depletion(
    *,
    initial_depletion_mm: float,
    taw_mm: float,
    raw_mm: float,
    etc_mm_day: float,
    days: int,
    daily_effective_rain_mm: dict[int, float] | None = None,
    irrigation_mm: float = 0.0,
    config: WaterBalanceConfig | None = None,
) -> list[dict[str, Any]]:
    """Roll the water balance forward, day by day.

    Used both by the planning horizon of the decision engine and by the what-if
    scenario simulator. Water uptake is reduced by Ks as the soil dries, which
    is what makes a deficit-irrigation scenario behave realistically instead of
    draining the reservoir linearly.

    The irrigation dose (if any) is applied at the start of day 0.
    """
    cfg = config or MODEL_CONFIG.water_balance
    rain = daily_effective_rain_mm or {}
    depletion = max(0.0, initial_depletion_mm - irrigation_mm)
    trajectory: list[dict[str, Any]] = []

    for day in range(days):
        depletion = max(0.0, depletion - rain.get(day, 0.0))

        if depletion <= raw_mm:
            ks = 1.0
        elif taw_mm > raw_mm:
            ks = max(0.0, (taw_mm - depletion) / (taw_mm - raw_mm))
        else:
            ks = 1.0

        actual_etc = ks * etc_mm_day
        depletion = min(taw_mm, depletion + actual_etc)

        # Ks at the END of the day, which is what the farmer will observe.
        if depletion <= raw_mm:
            ks_end = 1.0
        elif taw_mm > raw_mm:
            ks_end = max(0.0, (taw_mm - depletion) / (taw_mm - raw_mm))
        else:
            ks_end = 1.0

        trajectory.append(
            {
                "day_offset": day,
                "depletion_mm": round(depletion, 2),
                "effective_rain_mm": round(rain.get(day, 0.0), 2),
                "actual_etc_mm": round(actual_etc, 2),
                "ks": round(ks_end, 3),
                "stress_level": classify_stress(ks_end, cfg).value,
                "stress_label_fr": STRESS_LEVEL_FR[classify_stress(ks_end, cfg)],
            }
        )

    return trajectory
