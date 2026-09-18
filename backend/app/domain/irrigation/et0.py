"""Reference evapotranspiration (ET0).

Primary method: FAO-56 Penman-Monteith for a daily time step (Allen et al.,
1998, equation 6), the method recommended by the FAO as the sole standard for
computing ET0 from meteorological data.

Fallback: Hargreaves-Samani (FAO-56 equation 52), used ONLY when humidity,
wind or radiation are unavailable. FAO-56 explicitly sanctions this fallback
when the full data set is missing.

The module never invents a missing variable. Where FAO-56 documents a
procedure to *derive* a variable from others (e.g. solar radiation from the
daily temperature range, FAO-56 eq. 50), the derivation is applied and
recorded in ``ET0Result.warnings`` so the user can see it happened.

All functions are pure and deterministic — same inputs, same output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.domain.irrigation.constants import (
    ALBEDO_REFERENCE_CROP,
    COASTAL_DISTANCE_THRESHOLD_KM,
    DEFAULT_WIND_MEASUREMENT_HEIGHT_M,
    HARGREAVES_COEFFICIENT,
    KRS_COASTAL,
    KRS_INTERIOR,
    PSYCHROMETRIC_COEFFICIENT,
    RNL_CLOUD_A,
    RNL_CLOUD_B,
    RNL_EMISSIVITY_A,
    RNL_EMISSIVITY_B,
    RSO_A,
    RSO_B,
    SOLAR_CONSTANT_MJ_M2_MIN,
    STEFAN_BOLTZMANN_MJ_K4_M2_DAY,
    ET0Method,
)

__all__ = [
    "ET0Result",
    "atmospheric_pressure",
    "calculate_et0",
    "extraterrestrial_radiation",
    "net_radiation",
    "psychrometric_constant",
    "saturation_vapour_pressure",
    "slope_of_vapour_pressure_curve",
    "wind_speed_at_2m",
]


# --------------------------------------------------------------------------
# Intermediate physical quantities (FAO-56 chapter 3)
# --------------------------------------------------------------------------
def atmospheric_pressure(elevation_m: float) -> float:
    """Atmospheric pressure in kPa (FAO-56 eq. 7)."""
    return float(101.3 * (((293.0 - 0.0065 * elevation_m) / 293.0) ** 5.26))


def psychrometric_constant(elevation_m: float) -> float:
    """gamma in kPa/degC (FAO-56 eq. 8)."""
    return PSYCHROMETRIC_COEFFICIENT * atmospheric_pressure(elevation_m)


def saturation_vapour_pressure(temperature_c: float) -> float:
    """e0(T) in kPa (FAO-56 eq. 11)."""
    return 0.6108 * math.exp((17.27 * temperature_c) / (temperature_c + 237.3))


def slope_of_vapour_pressure_curve(temperature_c: float) -> float:
    """Delta in kPa/degC (FAO-56 eq. 13)."""
    return (4098.0 * saturation_vapour_pressure(temperature_c)) / (
        (temperature_c + 237.3) ** 2
    )


def wind_speed_at_2m(
    wind_speed_m_s: float, measurement_height_m: float = DEFAULT_WIND_MEASUREMENT_HEIGHT_M
) -> float:
    """Convert wind measured at z metres to the 2 m standard (FAO-56 eq. 47)."""
    if measurement_height_m == 2.0:
        return wind_speed_m_s
    return wind_speed_m_s * (4.87 / math.log(67.8 * measurement_height_m - 5.42))


def extraterrestrial_radiation(latitude_deg: float, day_of_year: int) -> float:
    """Ra in MJ m-2 day-1 (FAO-56 eq. 21).

    Depends only on latitude and date — it is astronomy, not weather, so it is
    always available and never has to be guessed.
    """
    phi = math.radians(latitude_deg)
    # Inverse relative distance Earth-Sun (eq. 23)
    dr = 1.0 + 0.033 * math.cos(2.0 * math.pi * day_of_year / 365.0)
    # Solar declination (eq. 24)
    declination = 0.409 * math.sin(2.0 * math.pi * day_of_year / 365.0 - 1.39)
    # Sunset hour angle (eq. 25), guarded for polar latitudes
    cos_ws = -math.tan(phi) * math.tan(declination)
    cos_ws = max(-1.0, min(1.0, cos_ws))
    omega_s = math.acos(cos_ws)

    return (
        (24.0 * 60.0 / math.pi)
        * SOLAR_CONSTANT_MJ_M2_MIN
        * dr
        * (
            omega_s * math.sin(phi) * math.sin(declination)
            + math.cos(phi) * math.cos(declination) * math.sin(omega_s)
        )
    )


def clear_sky_radiation(ra_mj: float, elevation_m: float) -> float:
    """Rso in MJ m-2 day-1 (FAO-56 eq. 37)."""
    return (RSO_A + RSO_B * elevation_m) * ra_mj


def solar_radiation_from_temperature_range(
    ra_mj: float, temp_max_c: float, temp_min_c: float, *, coastal: bool
) -> float:
    """Rs estimated from the daily temperature range (FAO-56 eq. 50).

    This is a documented FAO procedure, not an invention — but it is an
    estimate, and callers surface that fact to the user.
    """
    krs = KRS_COASTAL if coastal else KRS_INTERIOR
    delta_t = max(0.0, temp_max_c - temp_min_c)
    return krs * math.sqrt(delta_t) * ra_mj


def net_radiation(
    solar_radiation_mj: float,
    ra_mj: float,
    elevation_m: float,
    temp_max_c: float,
    temp_min_c: float,
    actual_vapour_pressure_kpa: float,
) -> tuple[float, float, float]:
    """Return (Rn, Rns, Rnl) in MJ m-2 day-1 (FAO-56 eq. 38, 39, 40)."""
    rns = (1.0 - ALBEDO_REFERENCE_CROP) * solar_radiation_mj

    rso = clear_sky_radiation(ra_mj, elevation_m)
    # Relative shortwave radiation, bounded per FAO-56 guidance (0.25-1.0)
    ratio = 1.0 if rso <= 0 else solar_radiation_mj / rso
    ratio = max(0.25, min(1.0, ratio))

    tmax_k4 = (temp_max_c + 273.16) ** 4
    tmin_k4 = (temp_min_c + 273.16) ** 4
    rnl = (
        STEFAN_BOLTZMANN_MJ_K4_M2_DAY
        * ((tmax_k4 + tmin_k4) / 2.0)
        * (RNL_EMISSIVITY_A + RNL_EMISSIVITY_B * math.sqrt(max(0.0, actual_vapour_pressure_kpa)))
        * (RNL_CLOUD_A * ratio + RNL_CLOUD_B)
    )
    rnl = max(0.0, rnl)
    return rns - rnl, rns, rnl


# --------------------------------------------------------------------------
# Result container
# --------------------------------------------------------------------------
@dataclass
class ET0Result:
    """ET0 with the full trace of how it was obtained.

    ``intermediates`` is what the "Pourquoi cette décision ?" panel shows: no
    number in this system is allowed to be untraceable.
    """

    et0_mm_day: float
    method: ET0Method
    inputs_used: dict[str, Any] = field(default_factory=dict)
    intermediates: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def method_label_fr(self) -> str:
        return {
            ET0Method.PENMAN_MONTEITH: "FAO-56 Penman-Monteith",
            ET0Method.HARGREAVES: "Hargreaves-Samani (méthode de repli)",
        }[self.method]

    def to_dict(self) -> dict[str, Any]:
        return {
            "et0_mm_day": round(self.et0_mm_day, 3),
            "method": self.method.value,
            "method_label_fr": self.method_label_fr,
            "inputs_used": self.inputs_used,
            "intermediates": {k: round(v, 4) for k, v in self.intermediates.items()},
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def calculate_et0(
    *,
    temp_max_c: float,
    temp_min_c: float,
    latitude_deg: float,
    day: date,
    temp_mean_c: float | None = None,
    relative_humidity_mean_pct: float | None = None,
    relative_humidity_max_pct: float | None = None,
    relative_humidity_min_pct: float | None = None,
    wind_speed_m_s: float | None = None,
    wind_measurement_height_m: float = DEFAULT_WIND_MEASUREMENT_HEIGHT_M,
    solar_radiation_mj_m2_day: float | None = None,
    elevation_m: float = 0.0,
    distance_to_coast_km: float | None = None,
) -> ET0Result:
    """Compute daily reference evapotranspiration in mm/day.

    Penman-Monteith is used whenever humidity AND wind are available (solar
    radiation may be derived from the temperature range per FAO-56 eq. 50).
    Otherwise the Hargreaves-Samani fallback is used, and the result says so.

    Raises:
        ValueError: if temperatures are missing or physically impossible. We
            refuse to fabricate a temperature.
    """
    if temp_max_c is None or temp_min_c is None:
        raise ValueError("temp_max_c et temp_min_c sont obligatoires pour calculer l'ET0.")
    if temp_max_c < temp_min_c:
        raise ValueError("temp_max_c ne peut pas être inférieure à temp_min_c.")
    if not -90.0 <= latitude_deg <= 90.0:
        raise ValueError("Latitude invalide.")

    warnings: list[str] = []
    tmean = temp_mean_c if temp_mean_c is not None else (temp_max_c + temp_min_c) / 2.0
    doy = day.timetuple().tm_yday
    ra = extraterrestrial_radiation(latitude_deg, doy)
    coastal = (
        distance_to_coast_km is not None
        and distance_to_coast_km <= COASTAL_DISTANCE_THRESHOLD_KM
    )

    inputs_used: dict[str, Any] = {
        "temp_max_c": temp_max_c,
        "temp_min_c": temp_min_c,
        "temp_mean_c": round(tmean, 2),
        "latitude_deg": latitude_deg,
        "elevation_m": elevation_m,
        "date": day.isoformat(),
    }

    # --- can we run Penman-Monteith? --------------------------------------
    # Comparaison à `None`, jamais test de véracité : une humidité relative de
    # 0 % est physiquement possible en conditions désertiques, et un `or`
    # l'aurait silencieusement traitée comme une valeur absente.
    available_humidity = [
        v
        for v in (
            relative_humidity_mean_pct,
            relative_humidity_max_pct,
            relative_humidity_min_pct,
        )
        if v is not None
    ]

    if not available_humidity or wind_speed_m_s is None:
        missing = []
        if not available_humidity:
            missing.append("humidité relative")
        if wind_speed_m_s is None:
            missing.append("vitesse du vent")
        warnings.append(
            "Méthode de repli Hargreaves-Samani utilisée : "
            + ", ".join(missing)
            + " non disponible(s). Précision réduite par rapport à Penman-Monteith."
        )
        return _hargreaves(
            temp_max_c=temp_max_c,
            temp_min_c=temp_min_c,
            tmean=tmean,
            ra=ra,
            inputs_used=inputs_used,
            warnings=warnings,
        )

    # --- vapour pressures (FAO-56 eq. 11, 12, 17, 19) ----------------------
    es = (saturation_vapour_pressure(temp_max_c) + saturation_vapour_pressure(temp_min_c)) / 2.0

    if relative_humidity_max_pct is not None and relative_humidity_min_pct is not None:
        # eq. 17 — the most accurate form
        ea = (
            saturation_vapour_pressure(temp_min_c) * relative_humidity_max_pct / 100.0
            + saturation_vapour_pressure(temp_max_c) * relative_humidity_min_pct / 100.0
        ) / 2.0
        inputs_used["relative_humidity_max_pct"] = relative_humidity_max_pct
        inputs_used["relative_humidity_min_pct"] = relative_humidity_min_pct
    else:
        # `available_humidity` n'est pas vide ici : la garde d'entrée l'a établi.
        if relative_humidity_mean_pct is not None:
            rh_mean = relative_humidity_mean_pct
        else:
            rh_mean = available_humidity[0]
            warnings.append(
                "Humidité relative moyenne estimée à partir de la seule valeur "
                "disponible."
            )
        # eq. 19
        ea = es * (rh_mean / 100.0)
        inputs_used["relative_humidity_mean_pct"] = rh_mean

    vpd = max(0.0, es - ea)

    # --- radiation ---------------------------------------------------------
    if solar_radiation_mj_m2_day is not None:
        rs = solar_radiation_mj_m2_day
        inputs_used["solar_radiation_mj_m2_day"] = round(rs, 3)
    else:
        rs = solar_radiation_from_temperature_range(
            ra, temp_max_c, temp_min_c, coastal=coastal
        )
        warnings.append(
            "Rayonnement solaire non fourni par l'API : estimé à partir de l'amplitude "
            f"thermique journalière (FAO-56 éq. 50, coefficient "
            f"{'côtier' if coastal else 'continental'})."
        )
    # Rs cannot exceed clear-sky radiation
    rso = clear_sky_radiation(ra, elevation_m)
    if rs > rso > 0:
        rs = rso

    rn, rns, rnl = net_radiation(rs, ra, elevation_m, temp_max_c, temp_min_c, ea)

    # --- aerodynamic and radiative terms (FAO-56 eq. 6) --------------------
    u2 = wind_speed_at_2m(wind_speed_m_s, wind_measurement_height_m)
    inputs_used["wind_speed_m_s"] = wind_speed_m_s
    inputs_used["wind_measurement_height_m"] = wind_measurement_height_m

    delta = slope_of_vapour_pressure_curve(tmean)
    gamma = psychrometric_constant(elevation_m)
    soil_heat_flux = 0.0  # G is negligible at a daily time step (FAO-56 eq. 42)

    numerator_radiation = 0.408 * delta * (rn - soil_heat_flux)
    numerator_aero = gamma * (900.0 / (tmean + 273.0)) * u2 * vpd
    denominator = delta + gamma * (1.0 + 0.34 * u2)

    et0 = (numerator_radiation + numerator_aero) / denominator
    et0 = max(0.0, et0)

    return ET0Result(
        et0_mm_day=et0,
        method=ET0Method.PENMAN_MONTEITH,
        inputs_used=inputs_used,
        intermediates={
            "extraterrestrial_radiation_ra": ra,
            "clear_sky_radiation_rso": rso,
            "solar_radiation_rs": rs,
            "net_shortwave_radiation_rns": rns,
            "net_longwave_radiation_rnl": rnl,
            "net_radiation_rn": rn,
            "saturation_vapour_pressure_es": es,
            "actual_vapour_pressure_ea": ea,
            "vapour_pressure_deficit": vpd,
            "slope_delta": delta,
            "psychrometric_gamma": gamma,
            "wind_speed_2m": u2,
            "radiation_term": numerator_radiation / denominator,
            "aerodynamic_term": numerator_aero / denominator,
        },
        # Pas de ligne « Rayonnement solaire : mesurée/API » ici. Une entrée
        # mesurée n'est pas un avertissement, et lorsqu'elle est estimée
        # l'avertissement détaillé ci-dessus le dit déjà — avec l'équation et le
        # coefficient. Deux lignes pour un seul fait apprennent au lecteur à
        # survoler la section, ce qui est exactement ce qu'elle ne doit pas
        # devenir.
        warnings=warnings,
    )


def _hargreaves(
    *,
    temp_max_c: float,
    temp_min_c: float,
    tmean: float,
    ra: float,
    inputs_used: dict[str, Any],
    warnings: list[str],
) -> ET0Result:
    """Hargreaves-Samani ET0 (FAO-56 eq. 52).

    ET0 = 0.0023 * (Tmean + 17.8) * (Tmax - Tmin)^0.5 * Ra
    with Ra expressed in equivalent mm/day (Ra_MJ * 0.408).
    """
    delta_t = max(0.0, temp_max_c - temp_min_c)
    ra_mm = ra * 0.408
    et0 = HARGREAVES_COEFFICIENT * (tmean + 17.8) * math.sqrt(delta_t) * ra_mm
    et0 = max(0.0, et0)
    return ET0Result(
        et0_mm_day=et0,
        method=ET0Method.HARGREAVES,
        inputs_used=inputs_used,
        intermediates={
            "extraterrestrial_radiation_ra": ra,
            "extraterrestrial_radiation_mm": ra_mm,
            "temperature_range": delta_t,
        },
        warnings=warnings,
    )
