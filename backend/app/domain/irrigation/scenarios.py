"""Deterministic what-if simulation of irrigation strategies.

The user asks "que se passe-t-il si je réduis l'irrigation de 20 % ?". We take
the recommended dose, scale it, apply it to the soil-water reservoir, and roll
the balance forward day by day with the crop's own demand and the expected
rainfall. The consequences reported are:

* water applied and water saved (m3),
* cost (when a tariff is configured),
* the stress trajectory over the horizon and the worst stress reached,
* an ESTIMATED yield impact — and only when the crop has a documented FAO-33
  yield response factor Ky. Otherwise the field is null and the UI says the
  estimate is unavailable rather than inventing a percentage.

Same inputs always give the same outputs: there is no randomness and no model
inference anywhere in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.formatting import fr
from app.domain.irrigation.constants import (
    MODEL_CONFIG,
    STRESS_LEVEL_FR,
    ScenarioConfig,
    StressLevel,
    WaterBalanceConfig,
)
from app.domain.irrigation.water_balance import project_depletion

__all__ = [
    "ScenarioComparison",
    "ScenarioResult",
    "compare_scenarios",
    "simulate_irrigation_scenario",
]


_STRESS_SEVERITY = {
    StressLevel.NORMAL: 0,
    StressLevel.MODERATE: 1,
    StressLevel.HIGH: 2,
    StressLevel.CRITICAL: 3,
}

_RISK_FR = {
    StressLevel.NORMAL: "Risque faible",
    StressLevel.MODERATE: "Risque modéré pour le rendement",
    StressLevel.HIGH: "Risque élevé : perte de rendement probable",
    StressLevel.CRITICAL: "Risque critique : dommages possibles à la culture",
}


@dataclass
class ScenarioResult:
    """Outcome of one irrigation strategy."""

    label_fr: str
    variation_pct: float  # -0.20 for "-20 %"
    volume_m3: float
    net_applied_mm: float
    water_saved_m3: float
    water_saved_pct: float
    estimated_cost: float | None
    cost_saved: float | None
    final_depletion_mm: float
    worst_stress_level: StressLevel
    days_under_stress: int
    trajectory: list[dict[str, Any]]
    yield_impact_pct: float | None
    yield_impact_note_fr: str
    risk_label_fr: str
    is_baseline: bool = False

    @property
    def worst_stress_label_fr(self) -> str:
        return STRESS_LEVEL_FR[self.worst_stress_level]

    def to_dict(self) -> dict[str, Any]:
        return {
            "label_fr": self.label_fr,
            "variation_pct": round(self.variation_pct, 3),
            "is_baseline": self.is_baseline,
            "volume_m3": round(self.volume_m3, 2),
            "net_applied_mm": round(self.net_applied_mm, 2),
            "water_saved_m3": round(self.water_saved_m3, 2),
            "water_saved_pct": round(self.water_saved_pct, 1),
            "estimated_cost": (
                round(self.estimated_cost, 2) if self.estimated_cost is not None else None
            ),
            "cost_saved": round(self.cost_saved, 2) if self.cost_saved is not None else None,
            "final_depletion_mm": round(self.final_depletion_mm, 2),
            "worst_stress_level": self.worst_stress_level.value,
            "worst_stress_label_fr": self.worst_stress_label_fr,
            "days_under_stress": self.days_under_stress,
            "trajectory": self.trajectory,
            "yield_impact_pct": (
                round(self.yield_impact_pct, 1)
                if self.yield_impact_pct is not None
                else None
            ),
            "yield_impact_note_fr": self.yield_impact_note_fr,
            "risk_label_fr": self.risk_label_fr,
        }


@dataclass
class ScenarioComparison:
    baseline_volume_m3: float
    horizon_days: int
    scenarios: list[ScenarioResult] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline_volume_m3": round(self.baseline_volume_m3, 2),
            "horizon_days": self.horizon_days,
            "scenarios": [s.to_dict() for s in self.scenarios],
            "assumptions": self.assumptions,
        }


def _estimate_yield_impact(
    *,
    trajectory: list[dict[str, Any]],
    etc_mm_day: float,
    ky: float | None,
) -> tuple[float | None, str]:
    """Estimate the relative yield loss over the simulated horizon.

    Uses the FAO-33 water production function (Doorenbos & Kassam, 1979):

        1 - Ya/Ym = Ky x (1 - ETa/ETm)

    ETa is the actual evapotranspiration accumulated over the horizon (already
    reduced by Ks in the trajectory), ETm the maximum (unstressed) demand.

    This is an ESTIMATE over a short horizon, not a validated yield prediction:
    FAO-33 Ky values apply to the whole growing season. When the crop has no
    documented Ky we return None instead of inventing a number.
    """
    if ky is None:
        return None, (
            "Impact sur le rendement non estimé : aucun coefficient de réponse Ky "
            "documenté (FAO-33) pour cette culture."
        )
    if etc_mm_day <= 0 or not trajectory:
        return None, "Impact sur le rendement non estimable : demande en eau nulle."

    eta = sum(d["actual_etc_mm"] for d in trajectory)
    etm = etc_mm_day * len(trajectory)
    if etm <= 0:
        return None, "Impact sur le rendement non estimable."

    relative_deficit = max(0.0, 1.0 - eta / etm)
    loss_pct = ky * relative_deficit * 100.0

    return loss_pct, (
        f"ESTIMATION indicative (fonction de production FAO-33, Ky = {ky}) : "
        f"ETa/ETm = {fr(eta, 1)}/{fr(etm, 1)} sur {len(trajectory)} jours. "
        "Le Ky de la FAO-33 est défini pour l'ensemble du cycle cultural : "
        "appliqué à un horizon court, ce chiffre est un ordre de grandeur, "
        "pas une prédiction de rendement validée."
    )


def simulate_irrigation_scenario(
    *,
    variation_pct: float,
    baseline_gross_volume_m3: float,
    baseline_net_mm: float,
    current_depletion_mm: float,
    total_available_water_mm: float,
    readily_available_water_mm: float,
    etc_mm_day: float,
    daily_effective_rain_mm: dict[int, float] | None = None,
    cost_per_m3: float | None = None,
    yield_response_factor_ky: float | None = None,
    horizon_days: int | None = None,
    label_fr: str | None = None,
    balance_config: WaterBalanceConfig | None = None,
    scenario_config: ScenarioConfig | None = None,
) -> ScenarioResult:
    """Simulate one irrigation strategy expressed as a % change of the dose."""
    scfg = scenario_config or MODEL_CONFIG.scenario
    horizon = horizon_days or scfg.simulation_horizon_days

    factor = 1.0 + variation_pct
    if factor < 0:
        raise ValueError("Une variation inférieure à -100 % n'a pas de sens.")

    volume = baseline_gross_volume_m3 * factor
    net_applied = baseline_net_mm * factor

    trajectory = project_depletion(
        initial_depletion_mm=current_depletion_mm,
        taw_mm=total_available_water_mm,
        raw_mm=readily_available_water_mm,
        etc_mm_day=etc_mm_day,
        days=horizon,
        daily_effective_rain_mm=daily_effective_rain_mm,
        irrigation_mm=net_applied,
        config=balance_config,
    )

    worst = StressLevel.NORMAL
    days_stressed = 0
    for day in trajectory:
        level = StressLevel(day["stress_level"])
        if _STRESS_SEVERITY[level] > _STRESS_SEVERITY[worst]:
            worst = level
        if level != StressLevel.NORMAL:
            days_stressed += 1

    saved_m3 = baseline_gross_volume_m3 - volume
    saved_pct = (saved_m3 / baseline_gross_volume_m3 * 100.0) if baseline_gross_volume_m3 else 0.0

    cost = volume * cost_per_m3 if cost_per_m3 is not None else None
    cost_saved = saved_m3 * cost_per_m3 if cost_per_m3 is not None else None

    yield_impact, yield_note = _estimate_yield_impact(
        trajectory=trajectory, etc_mm_day=etc_mm_day, ky=yield_response_factor_ky
    )

    if label_fr is None:
        if abs(variation_pct) < 1e-9:
            label_fr = "Plan actuel (100 %)"
        else:
            label_fr = f"{variation_pct:+.0%} d'irrigation"

    return ScenarioResult(
        label_fr=label_fr,
        variation_pct=variation_pct,
        volume_m3=volume,
        net_applied_mm=net_applied,
        water_saved_m3=saved_m3,
        water_saved_pct=saved_pct,
        estimated_cost=cost,
        cost_saved=cost_saved,
        final_depletion_mm=trajectory[-1]["depletion_mm"] if trajectory else current_depletion_mm,
        worst_stress_level=worst,
        days_under_stress=days_stressed,
        trajectory=trajectory,
        yield_impact_pct=yield_impact,
        yield_impact_note_fr=yield_note,
        risk_label_fr=_RISK_FR[worst],
        is_baseline=abs(variation_pct) < 1e-9,
    )


def compare_scenarios(
    *,
    baseline_gross_volume_m3: float,
    baseline_net_mm: float,
    current_depletion_mm: float,
    total_available_water_mm: float,
    readily_available_water_mm: float,
    etc_mm_day: float,
    variations: list[float] | None = None,
    daily_effective_rain_mm: dict[int, float] | None = None,
    cost_per_m3: float | None = None,
    yield_response_factor_ky: float | None = None,
    horizon_days: int | None = None,
    balance_config: WaterBalanceConfig | None = None,
    scenario_config: ScenarioConfig | None = None,
) -> ScenarioComparison:
    """Run the full set of what-if scenarios and return them side by side."""
    scfg = scenario_config or MODEL_CONFIG.scenario
    horizon = horizon_days or scfg.simulation_horizon_days
    variation_list = list(variations) if variations is not None else list(scfg.default_variations)

    results = [
        simulate_irrigation_scenario(
            variation_pct=v,
            baseline_gross_volume_m3=baseline_gross_volume_m3,
            baseline_net_mm=baseline_net_mm,
            current_depletion_mm=current_depletion_mm,
            total_available_water_mm=total_available_water_mm,
            readily_available_water_mm=readily_available_water_mm,
            etc_mm_day=etc_mm_day,
            daily_effective_rain_mm=daily_effective_rain_mm,
            cost_per_m3=cost_per_m3,
            yield_response_factor_ky=yield_response_factor_ky,
            horizon_days=horizon,
            balance_config=balance_config,
            scenario_config=scfg,
        )
        for v in variation_list
    ]

    assumptions = [
        f"La simulation projette le bilan hydrique sur {horizon} jours à partir de la "
        "situation actuelle de la parcelle.",
        "La demande de la culture (ETc) est supposée constante sur l'horizon, à partir "
        "de la météo du jour. Un changement de météo modifiera le résultat.",
        "La consommation réelle est réduite par le coefficient de stress Ks lorsque le "
        "sol s'assèche (FAO-56 éq. 84) : une parcelle stressée consomme moins d'eau.",
        "Les pluies prises en compte sont les pluies efficaces attendues, déjà pondérées "
        "par leur probabilité.",
        "Aucune nouvelle irrigation n'est simulée après l'apport initial.",
    ]

    return ScenarioComparison(
        baseline_volume_m3=baseline_gross_volume_m3,
        horizon_days=horizon,
        scenarios=results,
        assumptions=assumptions,
    )
