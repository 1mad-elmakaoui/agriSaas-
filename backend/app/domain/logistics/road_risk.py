"""Seuils de perturbation routière.

Distincts des seuils agronomiques, et c'est essentiel : ce sont deux phénomènes
différents qu'il serait faux de mesurer avec la même règle.

- Le **risque agricole** porte sur la culture au champ. Il s'évalue sur des
  cumuls longs (48 h et plus) : c'est la quantité d'eau reçue qui abîme un plant
  de tomate.
- Le **risque routier** porte sur la circulation d'un poids lourd. Il s'évalue
  sur l'**intensité** pendant la traversée (visibilité, aquaplanage) et sur la
  **saturation antérieure** des sols (ruissellement, coupures). Un cumul de
  60 mm étalé sur deux jours ne coupe pas une route ; 25 mm en une heure, si.

Comparer un cumul d'une heure de traversée à un seuil agronomique de 48 h
sous-estimerait systématiquement le risque routier. Ces seuils corrigent ce
défaut de catégorie.

Provenance : **valeurs de départ**, établies à partir des pratiques usuelles de
gestion de trafic — seuils de vigilance météo-routière, limites de renversement
des poids lourds. Aucune n'a été confrontée à un historique d'incidents
marocains, et le registre d'honnêteté le dit. Leur usage légitime est de classer
des itinéraires les uns par rapport aux autres, pas d'annoncer qu'une route sera
coupée.

Chaque critère porte son `rationale_fr` : c'est ce que le panneau « Sources et
preuves » affiche à côté du chiffre, pour qu'un seuil dépassé se conteste.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.enums import RiskLevel, RiskType

ROAD_THRESHOLD_SOURCE = (
    "Valeurs de départ AtlasAgri fondées sur les pratiques usuelles de "
    "vigilance météo-routière, à confirmer avec les gestionnaires de voirie"
)


@dataclass(frozen=True)
class RoadRiskCriterion:
    """Critère de perturbation routière à trois bornes."""

    risk_type: RiskType
    label_fr: str
    unit: str
    moderate_at: float
    high_at: float
    critical_at: float
    higher_is_worse: bool = True
    rationale_fr: str = ""

    def evaluate(self, value: float) -> RiskLevel:
        def crossed(bound: float) -> bool:
            return value >= bound if self.higher_is_worse else value <= bound

        if crossed(self.critical_at):
            return RiskLevel.CRITICAL
        if crossed(self.high_at):
            return RiskLevel.HIGH
        if crossed(self.moderate_at):
            return RiskLevel.MODERATE
        return RiskLevel.LOW

    def severity(self, value: float) -> float:
        """Sévérité continue dans [0,1], saturée au-delà du seuil critique."""
        if self.higher_is_worse:
            if value <= self.moderate_at:
                if not self.moderate_at:
                    return 0.0
                return max(0.0, min(0.25, 0.25 * value / self.moderate_at))
            if value >= self.critical_at:
                return 1.0
            return _interp(value, self.moderate_at, self.critical_at, 0.25, 1.0)

        if value >= self.moderate_at:
            return 0.0
        if value <= self.critical_at:
            return 1.0
        return _interp(value, self.moderate_at, self.critical_at, 0.25, 1.0)


def _interp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    if x1 == x0:
        return y1
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


# Intensité pendant la traversée : ce que le chauffeur affronte réellement.
RAIN_INTENSITY = RoadRiskCriterion(
    risk_type=RiskType.HEAVY_RAIN,
    label_fr="Intensité des précipitations",
    unit="mm/h",
    moderate_at=4.0,
    high_at=10.0,
    critical_at=20.0,
    rationale_fr=(
        "Au-delà de 4 mm/h la visibilité et l'adhérence se dégradent ; "
        "au-delà de 10 mm/h l'aquaplanage devient un risque réel pour un poids lourd."
    ),
)

# Saturation antérieure : ce qui provoque ruissellement et coupures.
RAIN_ACCUMULATION = RoadRiskCriterion(
    risk_type=RiskType.FLOOD,
    label_fr="Cumul de pluie sur 24 h avant passage",
    unit="mm",
    moderate_at=30.0,
    high_at=60.0,
    critical_at=100.0,
    rationale_fr=(
        "Des sols déjà saturés transforment une pluie modérée en ruissellement. "
        "C'est ce cumul antérieur, plus que la pluie du moment, qui coupe une chaussée."
    ),
)

# Renversement et déport : contrainte majeure pour un semi-remorque.
WIND_GUST = RoadRiskCriterion(
    risk_type=RiskType.STRONG_WIND,
    label_fr="Rafales de vent",
    unit="km/h",
    moderate_at=60.0,
    high_at=80.0,
    critical_at=100.0,
    rationale_fr=(
        "Au-delà de 80 km/h le risque de déport, voire de renversement, "
        "d'un véhicule à fort maître-couple devient significatif."
    ),
)

FROST = RoadRiskCriterion(
    risk_type=RiskType.FROST,
    label_fr="Température minimale",
    unit="°C",
    moderate_at=2.0,
    high_at=0.0,
    critical_at=-3.0,
    higher_is_worse=False,
    rationale_fr="Risque de verglas, particulièrement sur les sections d'altitude.",
)

ROAD_CRITERIA: tuple[RoadRiskCriterion, ...] = (
    RAIN_INTENSITY,
    RAIN_ACCUMULATION,
    WIND_GUST,
    FROST,
)


# Vulnérabilité structurelle par classe de route. Une autoroute à chaussées
# séparées, drainée et entretenue, encaisse ce qui coupe une route de montagne.
ROAD_CLASS_VULNERABILITY: dict[str, float] = {
    "autoroute": 0.85,
    "nationale": 1.10,
    "regionale": 1.30,
}


def vulnerability_of(road_class: str) -> float:
    return ROAD_CLASS_VULNERABILITY.get(road_class, 1.0)
