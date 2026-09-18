"""Agronomic model parameters.

Every threshold, factor and physical constant used by the irrigation engine
lives here so that none of them is buried inside an algorithm. Each entry
states where it comes from and, where it is a modelling choice rather than a
measured constant, says so explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

# --------------------------------------------------------------------------
# Physical constants (FAO-56, chapter 3)
# --------------------------------------------------------------------------
SOLAR_CONSTANT_MJ_M2_MIN = 0.0820  # Gsc
STEFAN_BOLTZMANN_MJ_K4_M2_DAY = 4.903e-9  # sigma
ALBEDO_REFERENCE_CROP = 0.23  # alpha, hypothetical grass reference surface
LATENT_HEAT_VAPORISATION_MJ_KG = 2.45
PSYCHROMETRIC_COEFFICIENT = 0.000665  # gamma = 0.000665 * P
DEFAULT_WIND_MEASUREMENT_HEIGHT_M = 10.0  # most weather APIs report 10 m wind

# Hargreaves-Samani coefficients (FAO-56 eq. 50 / eq. 52)
HARGREAVES_COEFFICIENT = 0.0023
KRS_INTERIOR = 0.16  # adjustment coefficient for interior locations
KRS_COASTAL = 0.19  # adjustment coefficient for coastal locations
# Distance to the coast (km) below which a field is treated as coastal.
COASTAL_DISTANCE_THRESHOLD_KM = 50.0

# Clear-sky radiation: Rso = (0.75 + 2e-5 * z) * Ra  (FAO-56 eq. 37)
RSO_A = 0.75
RSO_B = 2e-5

# Net longwave radiation coefficients (FAO-56 eq. 39)
RNL_EMISSIVITY_A = 0.34
RNL_EMISSIVITY_B = -0.14
RNL_CLOUD_A = 1.35
RNL_CLOUD_B = -0.35


class ET0Method(StrEnum):
    """Which ET0 formulation was actually used for a given day."""

    PENMAN_MONTEITH = "penman_monteith"
    HARGREAVES = "hargreaves"


class StressLevel(StrEnum):
    NORMAL = "normal"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


STRESS_LEVEL_FR = {
    StressLevel.NORMAL: "Normal",
    StressLevel.MODERATE: "Stress modéré",
    StressLevel.HIGH: "Stress élevé",
    StressLevel.CRITICAL: "Stress critique",
}

STRESS_LEVEL_COLOR = {
    StressLevel.NORMAL: "green",
    StressLevel.MODERATE: "orange",
    StressLevel.HIGH: "red",
    StressLevel.CRITICAL: "darkred",
}


class Recommendation(StrEnum):
    IRRIGATE = "IRRIGATE"
    MONITOR = "MONITOR"
    NO_IRRIGATION = "NO_IRRIGATION"
    POSTPONE_RAIN = "POSTPONE_RAIN"


RECOMMENDATION_FR = {
    Recommendation.IRRIGATE: "Irrigation recommandée",
    Recommendation.MONITOR: "À surveiller",
    Recommendation.NO_IRRIGATION: "Pas d'irrigation nécessaire",
    Recommendation.POSTPONE_RAIN: "Irrigation à reporter (pluie prévue)",
}


# La provenance d'une valeur n'est plus une énumération à un axe.
#
# `agriflow` portait un `DataSource` unique mêlant deux questions — *ce que la
# valeur est* (mesurée, calculée, estimée) et *d'où elle vient* (capteur, API,
# table de référence). Le produit fusionné les sépare : `DataState` et
# `DataOrigin`, dans `app.domain.enums`. Voir
# `docs/decisions/0004-vocabulaires-de-provenance.md`.
#
# La valeur `DEFAULT` de l'ancienne énumération a été supprimée plutôt que
# traduite : elle pesait 0,4 dans le score de qualité et servait de repli, ce qui
# est exactement « une valeur plausible substituée à une valeur manquante ». Une
# entrée absente rend désormais la sortie dépendante indisponible.


@dataclass(frozen=True)
class WaterBalanceConfig:
    """Thresholds of the soil-water-balance model.

    Stress is expressed through the FAO-56 water-stress coefficient Ks, which
    equals 1 as long as root-zone depletion stays within the readily available
    water (RAW) and decreases linearly to 0 at the wilting point.

    The mapping from Ks to the four stress classes is a PRESENTATION choice
    (FAO-56 does not define named classes); the boundaries are configurable.
    """

    # Ks >= this -> NORMAL (Ks == 1 means no stress at all)
    ks_normal_threshold: float = 0.999
    # Ks >= this -> MODERATE
    ks_moderate_threshold: float = 0.60
    # Ks >= this -> HIGH, below -> CRITICAL
    ks_high_threshold: float = 0.30

    # Adjust the tabulated depletion fraction p for the actual ETc
    # (FAO-56 eq. 84: p_adj = p + 0.04 * (5 - ETc)), clipped to [0.1, 0.8].
    adjust_p_for_etc: bool = True
    p_min: float = 0.10
    p_max: float = 0.80

    # Irrigation is triggered once projected depletion reaches this fraction
    # of RAW. 1.0 is the textbook trigger; a slightly lower value gives the
    # farmer a margin to organise the irrigation turn.
    irrigation_trigger_fraction_of_raw: float = 1.0
    # Below the trigger but above this fraction -> "à surveiller".
    monitor_fraction_of_raw: float = 0.75


@dataclass(frozen=True)
class RainfallConfig:
    """How rainfall is converted into water that actually reaches the roots.

    MODELLING CHOICE, documented rather than hidden: small rain events are
    intercepted by the canopy and evaporate without reaching the root zone, so
    they are discarded; the remainder is reduced by a runoff/interception
    factor. This is a simplified daily analogue of the USDA-SCS effective
    rainfall method, which is defined for monthly totals and is therefore not
    directly applicable to a daily decision.
    """

    # Daily rainfall below this is treated as fully intercepted (mm).
    interception_threshold_mm: float = 2.0
    # Fraction of the rainfall above the threshold that infiltrates.
    infiltration_fraction: float = 0.80

    # --- forecast handling -------------------------------------------------
    # A forecast is not a measurement. We never subtract every forecast
    # millimetre from the requirement. Instead:
    #   1. only days within `forecast_horizon_days` are considered;
    #   2. only forecasts with a probability >= `min_probability` count;
    #   3. the amount is weighted by its own probability;
    #   4. a global reliability discount is applied on top.
    forecast_horizon_days: int = 3
    min_probability: float = 0.50
    reliability_factor: float = 0.80
    # If this much effective rain is expected within the horizon, irrigation is
    # postponed rather than merely reduced (mm).
    postpone_threshold_mm: float = 10.0


@dataclass(frozen=True)
class IrrigationConfig:
    """Parameters of the irrigation-requirement calculation."""

    # Planning horizon: the crop keeps transpiring after today, so the dose
    # covers today's depletion plus the demand of the coming days.
    planning_horizon_days: int = 2
    # Never recommend a dose that the soil cannot absorb in one application.
    # Max depth = infiltration_rate * this many hours.
    max_application_hours: float = 4.0
    # Never recommend a dose smaller than this: it would evaporate before
    # being useful and wastes an irrigation turn (mm).
    min_useful_application_mm: float = 3.0
    # 1 mm applied over 1 hectare = 10 m3.
    mm_ha_to_m3: float = 10.0


@dataclass(frozen=True)
class ScenarioConfig:
    """What-if simulation parameters."""

    # Percentage changes offered by default, relative to the recommended dose.
    default_variations: tuple[float, ...] = (0.0, -0.10, -0.20, -0.30, 0.10, 0.20)
    # Number of days simulated forward after the irrigation event.
    simulation_horizon_days: int = 7


@dataclass(frozen=True)
class ModelConfig:
    water_balance: WaterBalanceConfig = field(default_factory=WaterBalanceConfig)
    rainfall: RainfallConfig = field(default_factory=RainfallConfig)
    irrigation: IrrigationConfig = field(default_factory=IrrigationConfig)
    scenario: ScenarioConfig = field(default_factory=ScenarioConfig)


MODEL_CONFIG = ModelConfig()
