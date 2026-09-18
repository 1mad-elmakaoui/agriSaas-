"""Irrigation requirement, duration and cost.

Conceptually (FAO-56 chapter 8 and FAO Irrigation Water Management manuals):

    trigger           = root-zone depletion projected over the planning horizon
                        vs. the readily available water (RAW)
    net requirement   = current root-zone depletion
                        - effective rainfall expected before the irrigation
    gross requirement = net requirement / application efficiency
    volume (m3)       = gross (mm) x area (ha) x 10
    duration (h)      = volume (m3) / flow rate (m3/h)
    cost (MAD)        = volume (m3) x cost per m3

Every intermediate value is returned so the "Pourquoi cette décision ?" panel
can show the whole chain. Nothing here is estimated by a language model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.formatting import fr, fr_pct
from app.domain.irrigation.constants import (
    MODEL_CONFIG,
    RECOMMENDATION_FR,
    IrrigationConfig,
    Recommendation,
    WaterBalanceConfig,
)

__all__ = [
    "IrrigationDuration",
    "IrrigationRequirement",
    "IrrigationSystemParameters",
    "WaterCost",
    "calculate_irrigation_duration",
    "calculate_irrigation_requirement",
    "calculate_water_cost",
    "format_duration_fr",
]


@dataclass(frozen=True)
class IrrigationSystemParameters:
    """Configuration of the field's irrigation equipment."""

    code: str
    name_fr: str
    efficiency: float
    flow_rate_m3_per_hour: float | None = None
    pump_capacity_m3_per_hour: float | None = None

    def __post_init__(self) -> None:
        if not 0 < self.efficiency <= 1:
            raise ValueError("L'efficience d'irrigation doit être comprise entre 0 et 1.")

    @property
    def usable_flow_rate(self) -> float | None:
        """The binding flow rate: the pump cannot deliver more than its capacity."""
        rates = [r for r in (self.flow_rate_m3_per_hour, self.pump_capacity_m3_per_hour) if r]
        return min(rates) if rates else None


@dataclass
class IrrigationRequirement:
    """Result of the irrigation-requirement calculation."""

    recommendation: Recommendation
    net_requirement_mm: float
    gross_requirement_mm: float
    volume_m3: float
    volume_liters: float
    efficiency: float
    field_area_ha: float
    projected_depletion_mm: float
    crop_demand_horizon_mm: float
    effective_rainfall_mm: float
    trigger_threshold_mm: float
    horizon_days: int
    steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    capped_by_infiltration: bool = False
    #: Vrai lorsque le quota du périmètre, et non l'agronomie, a fixé la dose.
    #: La distinction compte à l'écran : « voici ce qu'il faut » et « voici ce
    #: qu'on a le droit d'appliquer » ne se pilotent pas de la même façon.
    capped_by_quota: bool = False

    @property
    def recommendation_label_fr(self) -> str:
        return RECOMMENDATION_FR[self.recommendation]

    @property
    def should_irrigate(self) -> bool:
        return self.recommendation == Recommendation.IRRIGATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation": self.recommendation.value,
            "recommendation_label_fr": self.recommendation_label_fr,
            "net_requirement_mm": round(self.net_requirement_mm, 2),
            "gross_requirement_mm": round(self.gross_requirement_mm, 2),
            "volume_m3": round(self.volume_m3, 2),
            "volume_liters": round(self.volume_liters, 0),
            "efficiency": self.efficiency,
            "field_area_ha": self.field_area_ha,
            "projected_depletion_mm": round(self.projected_depletion_mm, 2),
            "crop_demand_horizon_mm": round(self.crop_demand_horizon_mm, 2),
            "effective_rainfall_mm": round(self.effective_rainfall_mm, 2),
            "trigger_threshold_mm": round(self.trigger_threshold_mm, 2),
            "horizon_days": self.horizon_days,
            "capped_by_infiltration": self.capped_by_infiltration,
            "capped_by_quota": self.capped_by_quota,
            "steps": self.steps,
            "warnings": self.warnings,
            "formulas": {
                "net": "besoin net = déficit actuel − pluie efficace attendue "
                       "(remise à la capacité au champ)",
                "gross": "besoin brut = besoin net / efficience du système",
                "volume": "volume (m³) = besoin brut (mm) × surface (ha) × 10",
            },
        }


def calculate_irrigation_requirement(
    *,
    current_depletion_mm: float,
    total_available_water_mm: float,
    readily_available_water_mm: float,
    etc_mm_day: float,
    field_area_ha: float,
    system: IrrigationSystemParameters,
    effective_rainfall_mm: float = 0.0,
    forecast_rainfall_postpones: bool = False,
    infiltration_rate_mm_per_hour: float | None = None,
    seasonal_quota_remaining_m3: float | None = None,
    config: IrrigationConfig | None = None,
    balance_config: WaterBalanceConfig | None = None,
) -> IrrigationRequirement:
    """Compute how much water this field needs, and whether to apply it now.

    Args:
        current_depletion_mm: root-zone depletion today (Dr).
        etc_mm_day: potential crop evapotranspiration per day.
        effective_rainfall_mm: rain we are willing to count on over the horizon
            (already discounted by probability — see ``water_balance``).
        forecast_rainfall_postpones: set when enough rain is expected to make
            irrigating today wasteful.
        seasonal_quota_remaining_m3: water still allocated to this field for the
            season, when the perimeter imposes a quota. ``None`` means no quota
            is configured — **not** an unlimited one, and not zero.

    A note on the quota, because it is the one place where this engine touches
    an arbitrage. The unification specification proposed weighting volume above
    yield for a field under quota. A quota is not a weight: it is a **hard
    limit**, and it belongs here, beside the infiltration cap, rather than in a
    scoring function. Weighting would let a high enough yield score authorise an
    application the perimeter does not allow. Capping cannot.
    """
    cfg = config or MODEL_CONFIG.irrigation
    bcfg = balance_config or MODEL_CONFIG.water_balance

    if field_area_ha <= 0:
        raise ValueError("La surface de la parcelle doit être strictement positive.")

    steps: list[str] = []
    warnings: list[str] = []
    horizon = cfg.planning_horizon_days

    # 1. Where will the reservoir be at the end of the planning horizon?
    crop_demand = etc_mm_day * horizon
    projected = current_depletion_mm + crop_demand - effective_rainfall_mm
    projected = max(0.0, min(total_available_water_mm, projected))
    steps.append(
        f"Déficit actuel {fr(current_depletion_mm, 1)} mm + besoin de la culture sur "
        f"{horizon} j ({fr(etc_mm_day, 2)} mm/j × {horizon} = {fr(crop_demand, 1)} mm) − pluie "
        f"efficace attendue {fr(effective_rainfall_mm, 1)} mm = déficit projeté "
        f"{fr(projected, 1)} mm."
    )

    # 2. Should we irrigate at all?
    trigger = readily_available_water_mm * bcfg.irrigation_trigger_fraction_of_raw
    monitor = readily_available_water_mm * bcfg.monitor_fraction_of_raw

    if forecast_rainfall_postpones:
        recommendation = Recommendation.POSTPONE_RAIN
        steps.append(
            "Des pluies significatives sont attendues dans les prochains jours : "
            "l'irrigation est reportée pour éviter un apport inutile."
        )
    elif projected >= trigger:
        recommendation = Recommendation.IRRIGATE
        steps.append(
            f"Déficit projeté ({fr(projected, 1)} mm) ≥ seuil de déclenchement "
            f"({fr(trigger, 1)} mm, soit la RFU) : irrigation recommandée."
        )
    elif projected >= monitor:
        recommendation = Recommendation.MONITOR
        steps.append(
            f"Déficit projeté ({fr(projected, 1)} mm) proche du seuil de déclenchement "
            f"({fr(trigger, 1)} mm) : surveiller la parcelle, irrigation probable sous peu."
        )
    else:
        recommendation = Recommendation.NO_IRRIGATION
        steps.append(
            f"Déficit projeté ({fr(projected, 1)} mm) < seuil de déclenchement "
            f"({fr(trigger, 1)} mm) : la réserve du sol suffit, pas d'irrigation nécessaire."
        )

    # 3. Net dose: refill the root zone back to field capacity — and no further.
    #
    # The projected deficit decides WHETHER to irrigate (so the turn is planned
    # slightly ahead of the stress threshold), but it must not set the DOSE:
    # the root zone can only hold today's deficit, so any millimetre beyond it
    # percolates below the roots and is lost. This matters most on shallow
    # soils, where the planning horizon can exceed the whole reservoir.
    if recommendation == Recommendation.IRRIGATE:
        net_mm = max(0.0, current_depletion_mm - effective_rainfall_mm)
        steps.append(
            f"Dose nette = déficit actuel {fr(current_depletion_mm, 1)} mm − pluie efficace "
            f"attendue {fr(effective_rainfall_mm, 1)} mm = {fr(net_mm, 1)} mm : la parcelle est "
            "ramenée à la capacité au champ, sans excédent qui percolerait au-delà "
            "des racines."
        )
    else:
        net_mm = 0.0

    capped = False
    if net_mm > 0 and infiltration_rate_mm_per_hour is None:
        # Ce qui manque manque, et se dit. Sans vitesse d'infiltration, la dose
        # n'est pas plafonnée : une dose supérieure à ce que le sol absorbe
        # ruissellerait, et rien dans le nombre affiché ne le laisserait voir.
        warnings.append(
            "La vitesse d'infiltration de ce sol n'est pas documentée : la dose "
            "n'a pas été plafonnée. Vérifiez qu'elle peut être absorbée en une "
            "application, ou fractionnez par précaution."
        )
    if net_mm > 0 and infiltration_rate_mm_per_hour:
        max_depth = infiltration_rate_mm_per_hour * cfg.max_application_hours
        if net_mm > max_depth:
            warnings.append(
                f"La dose calculée ({fr(net_mm, 1)} mm) dépasse ce que ce sol peut absorber "
                f"en une application ({fr(max_depth, 1)} mm sur "
                f"{fr(cfg.max_application_hours, 0)} h). "
                "Elle est plafonnée ; fractionnez l'irrigation en plusieurs apports."
            )
            # Le plafond est aussi une **étape**, pas seulement un
            # avertissement : sans elle, la trace passe de la dose nette à une
            # dose brute calculée sur un autre nombre, et le lecteur voit une
            # valeur apparaître de nulle part au milieu du raisonnement.
            steps.append(
                f"Dose plafonnée par la capacité d'infiltration du sol : "
                f"{fr(net_mm, 1)} mm ramenés à {fr(max_depth, 1)} mm "
                f"({fr(infiltration_rate_mm_per_hour, 1)} mm/h × "
                f"{fr(cfg.max_application_hours, 0)} h)."
            )
            net_mm = max_depth
            capped = True

    # Plafond de quota. Appliqué **après** le plafond d'infiltration et avant le
    # seuil d'utilité : c'est une contrainte du périmètre, pas une propriété du
    # sol, et elle ne doit pas pouvoir être contournée par un arbitrage.
    capped_by_quota = False
    if net_mm > 0 and seasonal_quota_remaining_m3 is not None:
        gross_for_quota = net_mm / system.efficiency
        volume_for_quota = gross_for_quota * field_area_ha * cfg.mm_ha_to_m3
        if seasonal_quota_remaining_m3 <= 0:
            warnings.append(
                "Quota saisonnier épuisé : aucun apport ne peut être recommandé "
                "sur cette parcelle. La décision revient au gestionnaire du "
                "périmètre."
            )
            net_mm = 0.0
            capped_by_quota = True
            recommendation = Recommendation.MONITOR
        elif volume_for_quota > seasonal_quota_remaining_m3:
            allowed_gross_mm = seasonal_quota_remaining_m3 / (
                field_area_ha * cfg.mm_ha_to_m3
            )
            allowed_net_mm = allowed_gross_mm * system.efficiency
            warnings.append(
                f"Dose plafonnée par le quota saisonnier : {fr(volume_for_quota, 0)} m³ "
                f"seraient nécessaires, {fr(seasonal_quota_remaining_m3, 0)} m³ restent "
                "alloués. L'apport recommandé ne couvre pas l'intégralité du "
                "déficit."
            )
            steps.append(
                f"Quota restant {fr(seasonal_quota_remaining_m3, 0)} m³ → dose nette "
                f"ramenée de {fr(net_mm, 1)} mm à {fr(allowed_net_mm, 1)} mm."
            )
            net_mm = allowed_net_mm
            capped_by_quota = True

    if 0 < net_mm < cfg.min_useful_application_mm:
        warnings.append(
            f"Dose calculée très faible ({fr(net_mm, 1)} mm), inférieure au seuil d'utilité "
            f"({cfg.min_useful_application_mm} mm) : un tel apport s'évapore avant "
            "d'atteindre les racines. Il est préférable d'attendre."
        )
        net_mm = 0.0
        recommendation = Recommendation.MONITOR

    # 4. Gross dose and volume.
    gross_mm = net_mm / system.efficiency if net_mm > 0 else 0.0
    if net_mm > 0:
        steps.append(
            f"Dose brute = {fr(net_mm, 1)} mm / {fr_pct(system.efficiency, 0)} d'efficience "
            f"({system.name_fr}) = {fr(gross_mm, 1)} mm."
        )

    volume_m3 = gross_mm * field_area_ha * cfg.mm_ha_to_m3
    if net_mm > 0:
        steps.append(
            f"Volume = {fr(gross_mm, 1)} mm × {fr(field_area_ha, 2)} ha × "
            f"{fr(cfg.mm_ha_to_m3, 0)} = {fr(volume_m3, 1)} m³."
        )

    return IrrigationRequirement(
        recommendation=recommendation,
        net_requirement_mm=net_mm,
        gross_requirement_mm=gross_mm,
        volume_m3=volume_m3,
        volume_liters=volume_m3 * 1000.0,
        efficiency=system.efficiency,
        field_area_ha=field_area_ha,
        projected_depletion_mm=projected,
        crop_demand_horizon_mm=crop_demand,
        effective_rainfall_mm=effective_rainfall_mm,
        trigger_threshold_mm=trigger,
        horizon_days=horizon,
        steps=steps,
        warnings=warnings,
        capped_by_infiltration=capped,
        capped_by_quota=capped_by_quota,
    )


def format_duration_fr(total_minutes: float) -> str:
    """Render a duration the way a farmer reads it: 2h14, 45 min."""
    minutes = round(total_minutes)
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h{mins:02d}"
    if hours:
        return f"{hours}h00"
    return f"{mins} min"


@dataclass
class IrrigationDuration:
    duration_minutes: float
    duration_hours: float
    duration_label_fr: str
    flow_rate_m3_per_hour: float
    volume_m3: float
    note_fr: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_minutes": round(self.duration_minutes, 1),
            "duration_hours": round(self.duration_hours, 2),
            "duration_label_fr": self.duration_label_fr,
            "flow_rate_m3_per_hour": self.flow_rate_m3_per_hour,
            "volume_m3": round(self.volume_m3, 2),
            "formula": "durée = volume requis / débit",
            "note_fr": self.note_fr,
        }


def calculate_irrigation_duration(
    *,
    volume_m3: float,
    flow_rate_m3_per_hour: float | None,
) -> IrrigationDuration | None:
    """Irrigation duration, or None when no flow rate is configured.

    Returning None is deliberate: without a measured flow rate there is no
    honest way to state a duration, so the UI shows "débit non renseigné"
    instead of a fabricated number.
    """
    if not flow_rate_m3_per_hour or flow_rate_m3_per_hour <= 0:
        return None
    if volume_m3 <= 0:
        return None

    hours = volume_m3 / flow_rate_m3_per_hour
    minutes = hours * 60.0
    return IrrigationDuration(
        duration_minutes=minutes,
        duration_hours=hours,
        duration_label_fr=format_duration_fr(minutes),
        flow_rate_m3_per_hour=flow_rate_m3_per_hour,
        volume_m3=volume_m3,
        note_fr=(
            f"{fr(volume_m3, 1)} m³ / {fr(flow_rate_m3_per_hour, 1)} m³/h = "
            f"{format_duration_fr(minutes)}."
        ),
    )


@dataclass
class WaterCost:
    estimated_cost: float
    cost_per_m3: float
    volume_m3: float
    currency: str = "MAD"

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_cost": round(self.estimated_cost, 2),
            "cost_per_m3": self.cost_per_m3,
            "volume_m3": round(self.volume_m3, 2),
            "currency": self.currency,
            "formula": "coût = volume (m³) × prix unitaire (MAD/m³)",
        }


def calculate_water_cost(
    *,
    volume_m3: float,
    cost_per_m3: float | None,
    currency: str = "MAD",
) -> WaterCost | None:
    """Estimated cost of the irrigation, or None if no tariff is configured."""
    if cost_per_m3 is None or cost_per_m3 < 0:
        return None
    return WaterCost(
        estimated_cost=volume_m3 * cost_per_m3,
        cost_per_m3=cost_per_m3,
        volume_m3=volume_m3,
        currency=currency,
    )
