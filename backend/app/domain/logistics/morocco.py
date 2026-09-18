"""Référentiel géographique marocain.

Ce module contient des **faits géographiques** : régions administratives
officielles, villes réelles avec leurs coordonnées réelles, et distances
routières de référence entre villes reliées par un axe existant.

Il ne contient aucune donnée métier (ni stock, ni fournisseur, ni expédition) :
celles-ci appartiennent à la base et à un tenant. Séparer les deux permet de
déployer le produit pour une autre organisation sans toucher au référentiel, et
d'étendre le référentiel à un autre pays sans toucher au métier.

Les distances sont des distances routières de référence, pas des distances à vol
d'oiseau. Elles sont suffisantes pour comparer des itinéraires entre eux ; elles
ne remplacent pas un moteur de routage pour du guidage.

**Ce que ces nombres sont, et ne sont pas.** Les coordonnées sont des faits
vérifiables. Les distances routières sont des ordres de grandeur relevés sur les
axes réels. `reliability` et `avg_speed_kmh`, en revanche, sont des **valeurs de
départ** : aucune n'a été mesurée sur le terrain marocain, et le registre
d'honnêteté le dit. Elles servent à classer des itinéraires les uns par rapport
aux autres, pas à prédire une heure d'arrivée.

Les sept régions décrites ici sont celles à enjeu agricole ou logistique. Le
référentiel de la base en porte neuf : cet écart est normal — une région peut
avoir des cultures sans avoir de nœud routier dans le graphe.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.geo import Coordinates


@dataclass(frozen=True)
class Region:
    """Région administrative marocaine."""

    code: str
    name_fr: str
    center: Coordinates
    agricultural_profile_fr: str
    main_crops: tuple[str, ...]


@dataclass(frozen=True)
class RoadNode:
    """Ville ou nœud logistique du réseau routier de référence."""

    code: str
    name_fr: str
    coordinates: Coordinates
    region_code: str
    is_logistics_hub: bool = False


@dataclass(frozen=True)
class RoadEdge:
    """Axe routier reliant deux nœuds.

    `reliability` traduit la robustesse opérationnelle de l'axe : une autoroute
    à chaussées séparées reste praticable dans des conditions où une route
    nationale de montagne ne l'est plus. C'est une caractéristique de
    l'infrastructure, distincte du risque météorologique du moment.
    """

    from_code: str
    to_code: str
    distance_km: float
    road_ref: str
    road_class: str  # autoroute | nationale | regionale
    avg_speed_kmh: float
    reliability: float = 0.9  # 0→1, robustesse structurelle de l'axe
    notes_fr: str = ""

    @property
    def nominal_duration_hours(self) -> float:
        return self.distance_km / self.avg_speed_kmh


# ---------------------------------------------------------------------------
# Régions (découpage administratif officiel du Maroc, 12 régions depuis 2015)
# Seules les régions à enjeu agricole ou logistique pour le produit sont
# décrites en détail ; les autres restent ajoutables sans changement de code.
# ---------------------------------------------------------------------------

REGIONS: dict[str, Region] = {
    "SOUSS_MASSA": Region(
        code="SOUSS_MASSA",
        name_fr="Souss-Massa",
        center=Coordinates(30.4278, -9.5981),
        agricultural_profile_fr=(
            "Premier bassin marocain de primeurs et d'agrumes destinés à l'export. "
            "Climat semi-aride à forte contrainte hydrique, production largement "
            "sous serre et sous irrigation."
        ),
        main_crops=("TOMATE", "AGRUME", "COURGETTE", "POIVRON", "BANANE"),
    ),
    "MARRAKECH_SAFI": Region(
        code="MARRAKECH_SAFI",
        name_fr="Marrakech-Safi",
        center=Coordinates(31.6295, -7.9811),
        agricultural_profile_fr=(
            "Zone de transit majeure entre le Sud et l'axe atlantique. "
            "Oléiculture, maraîchage et céréaliculture en bour. Le littoral de "
            "Safi et Essaouira bénéficie d'un climat plus tempéré."
        ),
        main_crops=("OLIVE", "TOMATE", "BLE", "AGRUME"),
    ),
    "CASABLANCA_SETTAT": Region(
        code="CASABLANCA_SETTAT",
        name_fr="Casablanca-Settat",
        center=Coordinates(33.5731, -7.5898),
        agricultural_profile_fr=(
            "Principal débouché de consommation et pôle logistique du pays. "
            "La plaine des Doukkala (El Jadida, Sidi Bennour) est une zone "
            "irriguée de betterave, maraîchage et céréales."
        ),
        main_crops=("BETTERAVE", "BLE", "TOMATE", "POMME_DE_TERRE"),
    ),
    "RABAT_SALE_KENITRA": Region(
        code="RABAT_SALE_KENITRA",
        name_fr="Rabat-Salé-Kénitra",
        center=Coordinates(34.0209, -6.8416),
        agricultural_profile_fr=(
            "Plaine du Gharb, l'une des zones les plus fertiles du Maroc, "
            "irriguée par le Sebou. Sensibilité connue aux inondations en "
            "période de fortes pluies hivernales."
        ),
        main_crops=("AGRUME", "BETTERAVE", "RIZ", "BLE", "FRAISE"),
    ),
    "BENI_MELLAL_KHENIFRA": Region(
        code="BENI_MELLAL_KHENIFRA",
        name_fr="Béni Mellal-Khénifra",
        center=Coordinates(32.3373, -6.3498),
        agricultural_profile_fr=(
            "Périmètre irrigué du Tadla, agrumiculture et betterave sucrière. "
            "Zone continentale exposée aux fortes chaleurs estivales."
        ),
        main_crops=("AGRUME", "BETTERAVE", "OLIVE", "BLE"),
    ),
    "FES_MEKNES": Region(
        code="FES_MEKNES",
        name_fr="Fès-Meknès",
        center=Coordinates(33.8935, -5.5473),
        agricultural_profile_fr=(
            "Plateau du Saïs, céréaliculture, oléiculture et viticulture. "
            "Exposition au gel en altitude durant l'hiver."
        ),
        main_crops=("BLE", "OLIVE", "RAISIN", "OIGNON"),
    ),
    "TANGER_TETOUAN": Region(
        code="TANGER_TETOUAN",
        name_fr="Tanger-Tétouan-Al Hoceïma",
        center=Coordinates(35.7595, -5.8340),
        agricultural_profile_fr=(
            "Débouché portuaire vers l'Europe via Tanger Med. Arrière-pays "
            "agricole du Loukkos, maraîchage et fruits rouges."
        ),
        main_crops=("FRAISE", "AGRUME", "TOMATE"),
    ),
}


# ---------------------------------------------------------------------------
# Réseau routier de référence
# ---------------------------------------------------------------------------

NODES: dict[str, RoadNode] = {
    n.code: n
    for n in [
        # --- Souss-Massa ---
        RoadNode("AGADIR", "Agadir", Coordinates(30.4278, -9.5981), "SOUSS_MASSA", True),
        RoadNode("INEZGANE", "Inezgane", Coordinates(30.3550, -9.5384), "SOUSS_MASSA"),
        RoadNode("AIT_MELLOUL", "Aït Melloul", Coordinates(30.3342, -9.4960), "SOUSS_MASSA"),
        RoadNode("TAROUDANT", "Taroudant", Coordinates(30.4703, -8.8770), "SOUSS_MASSA"),
        RoadNode("TIZNIT", "Tiznit", Coordinates(29.6974, -9.7316), "SOUSS_MASSA"),
        RoadNode("BIOUGRA", "Biougra", Coordinates(30.2131, -9.3706), "SOUSS_MASSA"),
        # --- Marrakech-Safi ---
        RoadNode("MARRAKECH", "Marrakech", Coordinates(31.6295, -7.9811), "MARRAKECH_SAFI", True),
        RoadNode("CHICHAOUA", "Chichaoua", Coordinates(31.5450, -8.7639), "MARRAKECH_SAFI"),
        RoadNode("IMINTANOUTE", "Imi n'Tanoute", Coordinates(31.1719, -8.8506), "MARRAKECH_SAFI"),
        RoadNode("ESSAOUIRA", "Essaouira", Coordinates(31.5085, -9.7595), "MARRAKECH_SAFI"),
        RoadNode("SAFI", "Safi", Coordinates(32.2994, -9.2372), "MARRAKECH_SAFI", True),
        RoadNode("YOUSSOUFIA", "Youssoufia", Coordinates(32.2464, -8.5292), "MARRAKECH_SAFI"),
        RoadNode("BENGUERIR", "Ben Guerir", Coordinates(32.2361, -7.9500), "MARRAKECH_SAFI"),
        # --- Casablanca-Settat ---
        RoadNode(
            "CASABLANCA", "Casablanca",
            Coordinates(33.5731, -7.5898), "CASABLANCA_SETTAT", True,
        ),
        RoadNode("MOHAMMEDIA", "Mohammedia", Coordinates(33.6866, -7.3830), "CASABLANCA_SETTAT"),
        RoadNode("ELJADIDA", "El Jadida", Coordinates(33.2549, -8.5069), "CASABLANCA_SETTAT", True),
        RoadNode(
            "SIDI_BENNOUR", "Sidi Bennour",
            Coordinates(32.6522, -8.4267), "CASABLANCA_SETTAT",
        ),
        RoadNode("SETTAT", "Settat", Coordinates(33.0011, -7.6166), "CASABLANCA_SETTAT"),
        RoadNode("BERRECHID", "Berrechid", Coordinates(33.2655, -7.5877), "CASABLANCA_SETTAT"),
        # --- Béni Mellal-Khénifra ---
        RoadNode(
            "BENI_MELLAL", "Béni Mellal",
            Coordinates(32.3373, -6.3498), "BENI_MELLAL_KHENIFRA",
        ),
        RoadNode(
            "FKIH_BEN_SALAH", "Fkih Ben Salah",
            Coordinates(32.5019, -6.6892), "BENI_MELLAL_KHENIFRA",
        ),
        RoadNode("KHOURIBGA", "Khouribga", Coordinates(32.8811, -6.9063), "BENI_MELLAL_KHENIFRA"),
        # --- Rabat-Salé-Kénitra ---
        RoadNode("RABAT", "Rabat", Coordinates(34.0209, -6.8416), "RABAT_SALE_KENITRA", True),
        RoadNode("KENITRA", "Kénitra", Coordinates(34.2610, -6.5802), "RABAT_SALE_KENITRA"),
        RoadNode("SIDI_KACEM", "Sidi Kacem", Coordinates(34.2214, -5.7081), "RABAT_SALE_KENITRA"),
        RoadNode(
            "SOUK_ARBAA", "Souk El Arbaa",
            Coordinates(34.6907, -5.9906), "RABAT_SALE_KENITRA",
        ),
        # --- Fès-Meknès ---
        RoadNode("MEKNES", "Meknès", Coordinates(33.8935, -5.5473), "FES_MEKNES"),
        RoadNode("FES", "Fès", Coordinates(34.0181, -5.0078), "FES_MEKNES", True),
        # --- Tanger-Tétouan ---
        RoadNode("LARACHE", "Larache", Coordinates(35.1932, -6.1557), "TANGER_TETOUAN"),
        RoadNode("TANGER", "Tanger", Coordinates(35.7595, -5.8340), "TANGER_TETOUAN", True),
        RoadNode("TANGER_MED", "Tanger Med", Coordinates(35.8869, -5.5017), "TANGER_TETOUAN", True),
    ]
}


# Axes routiers. Chaque arête est bidirectionnelle (voir `neighbours`).
EDGES: list[RoadEdge] = [
    # --- Corridor atlantique sud : Agadir → Marrakech → Casablanca (autoroute A7) ---
    RoadEdge("AGADIR", "INEZGANE", 13, "N1", "nationale", 45, 0.92),
    RoadEdge("INEZGANE", "AIT_MELLOUL", 8, "N10", "nationale", 45, 0.92),
    RoadEdge("AIT_MELLOUL", "TAROUDANT", 72, "N10", "nationale", 70, 0.88),
    RoadEdge("AIT_MELLOUL", "BIOUGRA", 22, "R105", "regionale", 55, 0.85),
    RoadEdge("AGADIR", "TIZNIT", 91, "N1", "nationale", 75, 0.88),
    RoadEdge("AGADIR", "IMINTANOUTE", 175, "A7", "autoroute", 100, 0.95,
             "Section de montagne de l'A7, sensible au brouillard et aux fortes pluies"),
    RoadEdge("IMINTANOUTE", "CHICHAOUA", 42, "A7", "autoroute", 105, 0.95),
    RoadEdge("CHICHAOUA", "MARRAKECH", 78, "A7", "autoroute", 110, 0.96),
    RoadEdge("MARRAKECH", "BENGUERIR", 72, "A7", "autoroute", 110, 0.96),
    RoadEdge("BENGUERIR", "SETTAT", 105, "A7", "autoroute", 110, 0.96),
    RoadEdge("SETTAT", "BERRECHID", 32, "A7", "autoroute", 110, 0.96),
    RoadEdge("BERRECHID", "CASABLANCA", 38, "A7", "autoroute", 100, 0.94),

    # --- Corridor littoral : Agadir → Essaouira → Safi → El Jadida → Casablanca (N1) ---
    RoadEdge("AGADIR", "ESSAOUIRA", 173, "N1", "nationale", 68, 0.82,
             "Route côtière sinueuse, exposée au vent et au brouillard marin"),
    RoadEdge("ESSAOUIRA", "SAFI", 125, "N1", "nationale", 70, 0.84),
    RoadEdge("SAFI", "ELJADIDA", 145, "N1", "nationale", 75, 0.86),
    RoadEdge("ELJADIDA", "CASABLANCA", 99, "A5", "autoroute", 105, 0.95),
    RoadEdge("ESSAOUIRA", "CHICHAOUA", 100, "N8", "nationale", 70, 0.83),
    RoadEdge("SAFI", "YOUSSOUFIA", 82, "N8", "nationale", 75, 0.86),
    RoadEdge("YOUSSOUFIA", "BENGUERIR", 62, "R206", "regionale", 65, 0.82),
    RoadEdge("SAFI", "MARRAKECH", 157, "N8", "nationale", 78, 0.87),

    # --- Doukkala ---
    RoadEdge("ELJADIDA", "SIDI_BENNOUR", 68, "N1", "nationale", 70, 0.86),
    RoadEdge("SIDI_BENNOUR", "SAFI", 96, "N1", "nationale", 72, 0.85),
    RoadEdge("ELJADIDA", "BERRECHID", 82, "R320", "regionale", 70, 0.84),
    RoadEdge("ELJADIDA", "MARRAKECH", 196, "N7", "nationale", 76, 0.85),

    # --- Axe atlantique nord ---
    RoadEdge("CASABLANCA", "MOHAMMEDIA", 28, "A3", "autoroute", 95, 0.94),
    RoadEdge("MOHAMMEDIA", "RABAT", 62, "A3", "autoroute", 110, 0.96),
    RoadEdge("RABAT", "KENITRA", 42, "A1", "autoroute", 110, 0.96),
    RoadEdge("KENITRA", "SOUK_ARBAA", 68, "A1", "autoroute", 110, 0.94,
             "Traverse la plaine du Gharb, sensible aux inondations hivernales"),
    RoadEdge("SOUK_ARBAA", "LARACHE", 63, "A1", "autoroute", 110, 0.94),
    RoadEdge("LARACHE", "TANGER", 88, "A1", "autoroute", 110, 0.95),
    RoadEdge("TANGER", "TANGER_MED", 45, "A4", "autoroute", 100, 0.95),
    RoadEdge("KENITRA", "SIDI_KACEM", 78, "N4", "nationale", 75, 0.85),
    RoadEdge("SIDI_KACEM", "MEKNES", 62, "N4", "nationale", 75, 0.86),
    RoadEdge("MEKNES", "FES", 62, "A2", "autoroute", 110, 0.96),
    RoadEdge("RABAT", "MEKNES", 138, "A2", "autoroute", 110, 0.96),

    # --- Intérieur : Tadla et plateau des phosphates ---
    RoadEdge("BERRECHID", "KHOURIBGA", 92, "N11", "nationale", 78, 0.87),
    RoadEdge("KHOURIBGA", "FKIH_BEN_SALAH", 78, "N11", "nationale", 75, 0.85),
    RoadEdge("FKIH_BEN_SALAH", "BENI_MELLAL", 45, "N8", "nationale", 75, 0.86),
    RoadEdge("BENI_MELLAL", "MARRAKECH", 178, "N8", "nationale", 78, 0.85),
    RoadEdge("BENI_MELLAL", "MEKNES", 232, "N8", "nationale", 72, 0.82),
]


# ---------------------------------------------------------------------------
# Accès au graphe
# ---------------------------------------------------------------------------

@dataclass
class _Adjacency:
    by_node: dict[str, list[RoadEdge]] = field(default_factory=dict)


def _build_adjacency() -> dict[str, list[RoadEdge]]:
    adj: dict[str, list[RoadEdge]] = {code: [] for code in NODES}
    for edge in EDGES:
        for code in (edge.from_code, edge.to_code):
            if code not in NODES:
                raise ValueError(
                    f"Axe routier incohérent : le nœud '{code}' n'existe pas dans NODES"
                )
        adj[edge.from_code].append(edge)
        # Arête inverse : le réseau est parcouru dans les deux sens.
        adj[edge.to_code].append(
            RoadEdge(
                from_code=edge.to_code,
                to_code=edge.from_code,
                distance_km=edge.distance_km,
                road_ref=edge.road_ref,
                road_class=edge.road_class,
                avg_speed_kmh=edge.avg_speed_kmh,
                reliability=edge.reliability,
                notes_fr=edge.notes_fr,
            )
        )
    return adj


ADJACENCY: dict[str, list[RoadEdge]] = _build_adjacency()


def neighbours(node_code: str) -> list[RoadEdge]:
    """Axes partant d'un nœud, dans les deux sens de circulation."""
    if node_code not in ADJACENCY:
        raise KeyError(f"Nœud routier inconnu : {node_code}")
    return ADJACENCY[node_code]


def nearest_node(point: Coordinates) -> RoadNode:
    """Nœud du réseau le plus proche d'un point quelconque.

    Permet de rattacher une ferme ou un entrepôt au réseau sans exiger qu'il
    coïncide exactement avec une ville.
    """
    from app.domain.geo import haversine_km

    return min(NODES.values(), key=lambda n: haversine_km(point, n.coordinates))


def region_of(node_code: str) -> Region:
    return REGIONS[NODES[node_code].region_code]
