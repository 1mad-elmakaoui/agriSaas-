"""Coût du transport routier.

Le coût doit être **explicable ligne à ligne**. Un exploitant qui lit
« +3 500 MAD » doit pouvoir savoir d'où viennent ces 3 500 dirhams, sinon il ne
fera pas confiance à l'arbitrage — et c'est l'arbitrage, pas le nombre, que ce
produit vend.

Les paramètres reflètent des ordres de grandeur du fret routier marocain. Ce sont
des **valeurs de départ** : aucune n'a été confrontée à une facture de
transporteur, et le registre d'honnêteté le dit. Elles sont destinées à devenir
une donnée d'organisation — une flotte propre et un affrètement externe n'ont pas
la même structure de coût.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.domain.enums import TransportMode
from app.domain.formatting import fr

__all__ = [
    "COST_PARAMETERS",
    "CostBreakdown",
    "CostLine",
    "TransportCostParameters",
    "estimate_cost",
]


@dataclass(frozen=True, slots=True)
class TransportCostParameters:
    cost_per_tonne_km_mad: float
    truck_capacity_tonnes: float
    #: Frais fixes **par camion** : location du véhicule, chauffeur, péages
    #: forfaitaires. Le code d'origine écrivait `fixed_cost_mad * trucks /
    #: max(1, trucks)`, expression qui vaut toujours `fixed_cost_mad` et
    #: contredit donc le commentaire qui l'accompagnait. L'une des deux était
    #: fausse ; c'est le calcul, parce qu'un convoi de huit frigorifiques ne
    #: coûte pas les frais fixes d'un seul. Le portage tranche en faveur du
    #: par-camion, et un test le fixe.
    fixed_cost_mad: float
    #: Le groupe froid consomme tant que le camion roule. C'est ce terme qui fait
    #: qu'un détour de deux heures coûte réellement plus cher, et pas seulement
    #: en kilomètres — sans lui, un itinéraire plus long mais mieux tracé
    #: paraîtrait gratuit.
    hourly_equipment_cost_mad: float = 0.0
    label_fr: str = ""


COST_PARAMETERS: dict[TransportMode, TransportCostParameters] = {
    TransportMode.ROAD_REFRIGERATED: TransportCostParameters(
        cost_per_tonne_km_mad=0.40,
        truck_capacity_tonnes=24.0,
        fixed_cost_mad=2500.0,
        hourly_equipment_cost_mad=55.0,
        label_fr="Camion frigorifique",
    ),
    TransportMode.ROAD_STANDARD: TransportCostParameters(
        cost_per_tonne_km_mad=0.35,
        truck_capacity_tonnes=26.0,
        fixed_cost_mad=2000.0,
        label_fr="Camion standard",
    ),
}


@dataclass(frozen=True, slots=True)
class CostLine:
    label_fr: str
    amount_mad: float


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    """Décomposition destinée à être affichée telle quelle."""

    distance_cost_mad: float
    fixed_cost_mad: float
    equipment_cost_mad: float
    trucks_required: int
    total_mad: float

    @property
    def lines_fr(self) -> tuple[CostLine, ...]:
        lines = [
            CostLine("Transport (distance × volume)", self.distance_cost_mad),
            CostLine(
                f"Frais fixes ({fr(self.trucks_required, 0)} camion(s))",
                self.fixed_cost_mad,
            ),
        ]
        if self.equipment_cost_mad > 0:
            lines.append(CostLine("Groupe froid (durée du trajet)", self.equipment_cost_mad))
        return tuple(lines)


def estimate_cost(
    *,
    distance_km: float,
    duration_hours: float,
    volume_tonnes: float,
    mode: TransportMode,
) -> CostBreakdown:
    """Estime le coût d'un trajet.

    Le nombre de camions est arrondi **au supérieur** : on n'affrète pas 7,5
    camions. Cet arrondi crée des effets de seuil réels que le modèle doit
    refléter — c'est précisément ce qui rend parfois une expédition fractionnée
    plus intéressante qu'un envoi unique, et un modèle continu le masquerait.
    """
    if volume_tonnes <= 0:
        raise ValueError("Le volume doit être strictement positif.")
    parameters = COST_PARAMETERS.get(mode)
    if parameters is None:
        # Ce qui manque manque : un mode sans paramètres de coût ne reçoit pas
        # ceux d'un autre mode « à peu près comparable ».
        raise ValueError(
            f"Aucun paramètre de coût pour le mode {mode.label_fr}. "
            "Aucune estimation n'est produite."
        )

    trucks = max(1, math.ceil(volume_tonnes / parameters.truck_capacity_tonnes))
    distance_cost = distance_km * volume_tonnes * parameters.cost_per_tonne_km_mad
    fixed_cost = parameters.fixed_cost_mad * trucks
    equipment_cost = parameters.hourly_equipment_cost_mad * duration_hours * trucks

    return CostBreakdown(
        distance_cost_mad=round(distance_cost, 0),
        fixed_cost_mad=round(fixed_cost, 0),
        equipment_cost_mad=round(equipment_cost, 0),
        trucks_required=trucks,
        total_mad=round(distance_cost + fixed_cost + equipment_cost, 0),
    )
