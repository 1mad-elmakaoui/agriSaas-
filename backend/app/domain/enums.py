"""Énumérations du domaine.

Deux règles gouvernent ce module :

1. **Les valeurs stockées sont en anglais**, les libellés affichés en français.
   Une question française sur « annulé » filtre sur `'cancelled'`, parce que
   c'est ce qui est dans la colonne. Un test le vérifie.
2. **Rien ici n'importe d'infrastructure.** Ce module est lu par un agronome
   qui ne lit pas de code web.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ORIGIN_BY_RELIABILITY",
    "Confidence",
    "CropCategory",
    "DataOrigin",
    "DataState",
    "GrowthStage",
    "ReliabilityLevel",
    "RiskLevel",
    "RiskType",
    "ShipmentStatus",
    "SiteType",
    "StageSource",
    "TransportMode",
    "UserRole",
]


class UserRole(StrEnum):
    """Rôles applicatifs.

    `AGRONOME` est le rôle qui calibre les seuils et approuve les surcharges de
    référentiel : c'est la seule personne autorisée à déclarer qu'une valeur
    agronomique documentée est remplacée par une mesure locale.
    """

    ADMIN = "ADMIN"
    SUPPLY_CHAIN_MANAGER = "SUPPLY_CHAIN_MANAGER"
    OPERATIONS_MANAGER = "OPERATIONS_MANAGER"
    AGRONOME = "AGRONOME"
    ANALYST = "ANALYST"
    EXECUTIVE = "EXECUTIVE"

    @property
    def label_fr(self) -> str:
        return _ROLE_FR[self]


_ROLE_FR: dict[UserRole, str] = {
    UserRole.ADMIN: "Administrateur",
    UserRole.SUPPLY_CHAIN_MANAGER: "Responsable chaîne d'approvisionnement",
    UserRole.OPERATIONS_MANAGER: "Responsable d'exploitation",
    UserRole.AGRONOME: "Agronome",
    UserRole.ANALYST: "Analyste",
    UserRole.EXECUTIVE: "Direction",
}


class DataState(StrEnum):
    """Statut épistémique d'une valeur : *ce qu'elle est*.

    Répond à « cette valeur a-t-elle été mesurée ? ». Orthogonal à
    :class:`DataOrigin`, qui répond à « d'où vient-elle ? ». Les aplatir en une
    seule énumération ferait perdre l'une des deux questions — c'est la raison
    d'être des deux axes.
    """

    OBSERVED = "OBSERVED"
    FORECAST = "FORECAST"
    DERIVED = "DERIVED"
    INFERRED = "INFERRED"
    SIMULATED = "SIMULATED"

    @property
    def label_fr(self) -> str:
        return _STATE_FR[self]

    @property
    def is_measurement(self) -> bool:
        """Vrai uniquement pour une valeur constatée, jamais pour un calcul."""
        return self is DataState.OBSERVED


_STATE_FR: dict[DataState, str] = {
    DataState.OBSERVED: "Observé",
    DataState.FORECAST: "Prévu",
    DataState.DERIVED: "Calculé",
    DataState.INFERRED: "Estimé",
    DataState.SIMULATED: "Simulé",
}


class DataOrigin(StrEnum):
    """Provenance d'une valeur : *d'où elle vient*.

    Il n'existe volontairement **pas** de valeur « par défaut ». `agriflow`
    portait un `DataSource.DEFAULT` pesant 0,4 dans son score de qualité : une
    valeur plausible substituée à une valeur manquante, c'est-à-dire la règle
    « ce qui manque manque » contournée. Une entrée absente rend la sortie
    dépendante indisponible ; elle ne prend pas une valeur de repli.
    """

    SENSOR = "SENSOR"
    MANUAL_ENTRY = "MANUAL_ENTRY"
    EXTERNAL_API = "EXTERNAL_API"
    REFERENCE_TABLE = "REFERENCE_TABLE"
    MODEL = "MODEL"
    SEED_DEMO = "SEED_DEMO"

    @property
    def label_fr(self) -> str:
        return _ORIGIN_FR[self]

    @property
    def reliability_weight(self) -> float:
        return _ORIGIN_WEIGHT[self]


_ORIGIN_FR: dict[DataOrigin, str] = {
    DataOrigin.SENSOR: "Capteur",
    DataOrigin.MANUAL_ENTRY: "Saisie manuelle",
    DataOrigin.EXTERNAL_API: "Service externe",
    DataOrigin.REFERENCE_TABLE: "Référentiel agronomique",
    DataOrigin.MODEL: "Modèle de calcul",
    DataOrigin.SEED_DEMO: "Jeu de démonstration",
}

#: Pondération de fiabilité, ordonnée strictement décroissante.
#:
#: L'ordre — capteur > saisie > service externe > modèle > démonstration — est
#: la raison pour laquelle le tenant de démonstration affiche « Fiabilité :
#: Moyenne » plutôt qu'« Élevée ». Un test vérifie la stricte décroissance :
#: deux origines de même poids rendraient le score aveugle à une dégradation.
_ORIGIN_WEIGHT: dict[DataOrigin, float] = {
    DataOrigin.SENSOR: 1.00,
    DataOrigin.MANUAL_ENTRY: 0.85,
    DataOrigin.EXTERNAL_API: 0.75,
    DataOrigin.REFERENCE_TABLE: 0.65,
    DataOrigin.MODEL: 0.55,
    DataOrigin.SEED_DEMO: 0.30,
}

#: Ordre canonique, du plus fiable au moins fiable.
ORIGIN_BY_RELIABILITY: tuple[DataOrigin, ...] = tuple(
    sorted(DataOrigin, key=lambda o: _ORIGIN_WEIGHT[o], reverse=True)
)


class ReliabilityLevel(StrEnum):
    """Classe de fiabilité affichée. Présentation, pas mesure."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @property
    def label_fr(self) -> str:
        return {
            ReliabilityLevel.HIGH: "Élevée",
            ReliabilityLevel.MEDIUM: "Moyenne",
            ReliabilityLevel.LOW: "Faible",
        }[self]


class SiteType(StrEnum):
    """Nature d'un site.

    Une ferme, un entrepôt, une plateforme et un client partagent coordonnées,
    région et capacité. Trois tables dupliqueraient la même colonne de
    géolocalisation — et le moteur d'itinéraire devrait connaître les trois.
    """

    FARM = "FARM"
    WAREHOUSE = "WAREHOUSE"
    HUB = "HUB"
    CUSTOMER = "CUSTOMER"

    @property
    def label_fr(self) -> str:
        return {
            SiteType.FARM: "Exploitation",
            SiteType.WAREHOUSE: "Entrepôt",
            SiteType.HUB: "Plateforme",
            SiteType.CUSTOMER: "Client",
        }[self]


class CropCategory(StrEnum):
    """Famille culturale. Sert au regroupement d'écrans, jamais à un calcul."""

    ARBORICULTURE = "ARBORICULTURE"
    MARAICHAGE = "MARKET_GARDEN"
    CEREALE = "CEREAL"
    PETIT_FRUIT = "SOFT_FRUIT"
    VIGNE = "VINE"

    @property
    def label_fr(self) -> str:
        return {
            CropCategory.ARBORICULTURE: "Arboriculture",
            CropCategory.MARAICHAGE: "Maraîchage",
            CropCategory.CEREALE: "Céréale",
            CropCategory.PETIT_FRUIT: "Petit fruit",
            CropCategory.VIGNE: "Vigne",
        }[self]


class GrowthStage(StrEnum):
    """Les quatre stades de la FAO-56 (figure 21).

    Kc est constant sur `INITIAL` et `MID_SEASON`, et interpolé linéairement sur
    `DEVELOPMENT` et `LATE_SEASON`. L'ordre est porté par `sequence` et non par
    l'ordre de déclaration : une énumération réordonnée par mégarde changerait
    l'interpolation sans que rien ne le signale.
    """

    INITIAL = "INITIAL"
    DEVELOPMENT = "DEVELOPMENT"
    MID_SEASON = "MID_SEASON"
    LATE_SEASON = "LATE_SEASON"

    @property
    def sequence(self) -> int:
        return {
            GrowthStage.INITIAL: 1,
            GrowthStage.DEVELOPMENT: 2,
            GrowthStage.MID_SEASON: 3,
            GrowthStage.LATE_SEASON: 4,
        }[self]

    @property
    def label_fr(self) -> str:
        return {
            GrowthStage.INITIAL: "Initial",
            GrowthStage.DEVELOPMENT: "Développement",
            GrowthStage.MID_SEASON: "Mi-saison",
            GrowthStage.LATE_SEASON: "Arrière-saison",
        }[self]


class StageSource(StrEnum):
    """D'où vient le stade retenu pour une parcelle.

    La distinction est agronomique, pas cosmétique : un stade **déclaré** par
    l'exploitant fait foi, un stade **estimé** depuis la date de plantation est
    une hypothèse qui se trompe dès qu'une saison est atypique. Les confondre
    dans un seul chemin de code ferait disparaître l'information au moment
    précis où elle compte — l'affichage du Kc retenu.
    """

    DECLARED = "DECLARED"
    ESTIMATED = "ESTIMATED"

    @property
    def label_fr(self) -> str:
        return {
            StageSource.DECLARED: "Déclaré par l'exploitant",
            StageSource.ESTIMATED: "Estimé depuis la date de plantation",
        }[self]


class TransportMode(StrEnum):
    ROAD_REFRIGERATED = "ROAD_REFRIGERATED"
    ROAD_STANDARD = "ROAD_STANDARD"
    RAIL = "RAIL"
    SEA = "SEA"

    @property
    def label_fr(self) -> str:
        return {
            TransportMode.ROAD_REFRIGERATED: "Route frigorifique",
            TransportMode.ROAD_STANDARD: "Route standard",
            TransportMode.RAIL: "Rail",
            TransportMode.SEA: "Maritime",
        }[self]


class ShipmentStatus(StrEnum):
    """Statuts d'expédition.

    Les valeurs stockées sont anglaises : une question française sur « annulé »
    filtre sur `'CANCELLED'`, parce que c'est ce que contient la colonne et ce
    que la vue analytique expose. Un test le vérifie.
    """

    PLANNED = "PLANNED"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    DELAYED = "DELAYED"
    CANCELLED = "CANCELLED"

    @property
    def label_fr(self) -> str:
        return {
            ShipmentStatus.PLANNED: "Planifiée",
            ShipmentStatus.IN_TRANSIT: "En transit",
            ShipmentStatus.DELIVERED: "Livrée",
            ShipmentStatus.DELAYED: "Retardée",
            ShipmentStatus.CANCELLED: "Annulée",
        }[self]


class RiskType(StrEnum):
    """Nature d'une perturbation routière.

    Distincte du risque agronomique : ce sont deux phénomènes qu'il serait faux
    de mesurer avec la même règle. Le risque agricole porte sur la culture au
    champ et s'évalue sur des cumuls longs ; le risque routier porte sur la
    circulation d'un poids lourd et s'évalue sur l'intensité pendant la
    traversée et sur la saturation antérieure des sols.
    """

    HEAVY_RAIN = "HEAVY_RAIN"
    FLOOD = "FLOOD"
    STRONG_WIND = "STRONG_WIND"
    FROST = "FROST"

    @property
    def label_fr(self) -> str:
        return {
            RiskType.HEAVY_RAIN: "Fortes précipitations",
            RiskType.FLOOD: "Ruissellement et inondation",
            RiskType.STRONG_WIND: "Vent fort",
            RiskType.FROST: "Gel",
        }[self]


class RiskLevel(StrEnum):
    """Les quatre niveaux, fixés par le glossaire et employés partout.

    Ils portent la couleur sémantique de l'interface — vert, jaune, orange,
    rouge — et la légende de la carte. Un cinquième niveau introduit ici
    obligerait à inventer une couleur, donc à casser la lecture.
    """

    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def label_fr(self) -> str:
        return {
            RiskLevel.LOW: "Risque faible",
            RiskLevel.MODERATE: "Risque modéré",
            RiskLevel.HIGH: "Risque élevé",
            RiskLevel.CRITICAL: "Risque critique",
        }[self]

    @property
    def rank(self) -> int:
        return {
            RiskLevel.LOW: 0,
            RiskLevel.MODERATE: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.CRITICAL: 3,
        }[self]

    @classmethod
    def from_severity(cls, severity: float) -> RiskLevel:
        """Sévérité continue → classe.

        Les bornes sont un choix de **présentation**, pas une propriété
        physique : elles décident quand un exploitant voit passer une pastille
        de vert à jaune. Les changer ne change aucun calcul.
        """
        if severity >= 0.75:
            return cls.CRITICAL
        if severity >= 0.5:
            return cls.HIGH
        if severity >= 0.25:
            return cls.MODERATE
        return cls.LOW


class Confidence(StrEnum):
    """Confiance dans une évaluation d'itinéraire.

    Distincte de `ReliabilityLevel`, qui note la provenance des valeurs
    d'entrée. Ici on note la **couverture** : un itinéraire dont la moitié des
    tronçons n'a pas de météo ne peut pas être évalué avec confiance, même si
    l'autre moitié vient d'un capteur.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"

    @property
    def label_fr(self) -> str:
        return {
            Confidence.LOW: "Faible",
            Confidence.MEDIUM: "Moyenne",
            Confidence.HIGH: "Élevée",
        }[self]
