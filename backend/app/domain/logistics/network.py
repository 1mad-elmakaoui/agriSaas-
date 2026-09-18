"""Itinéraires sur le graphe routier de référence.

Dijkstra sur les nœuds et axes de `morocco.py`, et génération d'alternatives par
**pénalisation itérative** : on calcule le meilleur trajet, on pénalise ses
arêtes, on recalcule. C'est ce qui produit des corridors réellement différents —
montagne, littoral, intérieur — là où une recherche des k plus courts chemins
rendrait k variantes du même trajet, dont l'utilisateur ne pourrait rien faire.

Le coût minimisé est la **durée**, pas la distance : un détour autoroutier plus
long en kilomètres est souvent plus rapide, et c'est la durée qui compte pour une
marchandise périssable.

Ce module ne calcule aucun risque et n'attribue aucun score. Il produit une
géométrie et des attributs de trajet ; l'exposition et le classement vivent
ailleurs. Mélanger les deux rendrait impossible de changer de source de tracé
sans réécrire la logique métier.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from itertools import pairwise

from app.domain.logistics.morocco import EDGES, NODES, RoadEdge, neighbours

__all__ = [
    "RouteGeometry",
    "RouteSegment",
    "RoutingError",
    "alternative_routes",
    "shortest_route",
]

#: Facteur appliqué aux arêtes déjà empruntées lors de la recherche
#: d'alternatives. En dessous, les alternatives rejouent le même corridor ;
#: au-dessus, elles deviennent absurdement longues. Valeur de départ, non
#: calibrée sur des itinéraires réellement empruntés.
DIVERSITY_PENALTY = 2.6

#: Une alternative dont la durée dépasse ce multiple du meilleur trajet n'est pas
#: une option qu'un exploitant envisagerait. La proposer quand même ferait perdre
#: confiance dans toute la liste : trois alternatives crédibles valent mieux que
#: cinq dont deux sont absurdes.
MAX_DURATION_RATIO = 1.75

#: Deux itinéraires partageant plus que cette proportion de leurs nœuds
#: intermédiaires décrivent le même corridor. Les présenter côte à côte donnerait
#: une illusion de choix.
MAX_NODE_OVERLAP = 0.7


class RoutingError(ValueError):
    """Aucun itinéraire, ou un nœud inconnu. Traduit en français à la frontière."""


@dataclass(frozen=True, slots=True)
class RouteSegment:
    """Tronçon élémentaire.

    Le grain « tronçon » n'est pas un détail d'implémentation : un itinéraire
    n'est presque jamais exposé sur toute sa longueur, et sans segments on ne
    peut ni situer la zone problématique sur la carte, ni dire à quelle heure le
    véhicule y passe.
    """

    from_code: str
    from_name_fr: str
    from_latitude: float
    from_longitude: float
    to_code: str
    to_name_fr: str
    to_latitude: float
    to_longitude: float
    distance_km: float
    duration_hours: float
    road_ref: str
    road_class: str
    reliability: float
    notes_fr: str = ""


@dataclass(frozen=True, slots=True)
class RouteGeometry:
    """Un itinéraire complet, tel que le graphe le décrit.

    Ne porte **aucun** score de risque : c'est un fait géographique, pas une
    évaluation.
    """

    id: str
    label_fr: str
    node_codes: tuple[str, ...]
    segments: tuple[RouteSegment, ...]
    total_distance_km: float
    total_duration_hours: float

    @property
    def average_reliability(self) -> float:
        """Fiabilité pondérée par la distance.

        Une moyenne simple donnerait le même poids à un tronçon urbain de 8 km
        qu'à 175 km de montagne.
        """
        total = sum(s.distance_km for s in self.segments)
        if total <= 0:
            return 1.0
        return sum(s.reliability * s.distance_km for s in self.segments) / total

    @property
    def path_lonlat(self) -> list[tuple[float, float]]:
        """Tracé en (longitude, latitude), prêt pour la carte.

        Le graphe relie les villes en ligne droite : c'est suffisant pour
        comparer des corridors, et insuffisant pour du guidage. L'interface ne
        doit pas laisser croire l'inverse.
        """
        if not self.segments:
            return []
        points = [(self.segments[0].from_longitude, self.segments[0].from_latitude)]
        points.extend((s.to_longitude, s.to_latitude) for s in self.segments)
        return points


def shortest_route(origin_code: str, destination_code: str) -> RouteGeometry:
    _validate(origin_code, destination_code)
    path = _dijkstra(origin_code, destination_code)
    if path is None:
        raise RoutingError(
            f"Aucun itinéraire routier entre {_name(origin_code)} et "
            f"{_name(destination_code)}."
        )
    return _build(path, route_id="itineraire-direct", label_fr="Itinéraire le plus rapide")


def alternative_routes(
    origin_code: str, destination_code: str, *, max_routes: int = 3
) -> list[RouteGeometry]:
    """Corridors distincts entre deux nœuds, le plus rapide en premier."""
    _validate(origin_code, destination_code)

    penalties: dict[tuple[str, str], float] = {}
    routes: list[RouteGeometry] = []
    seen: set[tuple[str, ...]] = set()

    # Marge d'itérations : certaines tentatives redonnent un chemin déjà vu.
    for _ in range(max_routes * 3):
        if len(routes) >= max_routes:
            break
        path = _dijkstra(origin_code, destination_code, penalties=penalties)
        if path is None:
            break

        key = tuple(path)
        if key not in seen:
            seen.add(key)
            candidate = _build(
                path,
                route_id=f"itineraire-{len(routes) + 1}",
                label_fr=_label(path, len(routes)),
            )
            if _is_credible(candidate, routes):
                routes.append(candidate)

        for a, b in pairwise(path):
            for edge_key in ((a, b), (b, a)):
                penalties[edge_key] = penalties.get(edge_key, 1.0) * DIVERSITY_PENALTY

    if not routes:
        raise RoutingError(
            f"Aucun itinéraire disponible entre {_name(origin_code)} et "
            f"{_name(destination_code)}."
        )
    return routes


# --- interne ---------------------------------------------------------------


def _validate(origin_code: str, destination_code: str) -> None:
    for code in (origin_code, destination_code):
        if code not in NODES:
            raise RoutingError(f"Nœud routier inconnu : « {code} ».")
    if origin_code == destination_code:
        raise RoutingError("L'origine et la destination sont identiques.")


def _dijkstra(
    origin: str,
    destination: str,
    *,
    penalties: dict[tuple[str, str], float] | None = None,
) -> list[str] | None:
    penalties = penalties or {}
    best: dict[str, float] = {origin: 0.0}
    previous: dict[str, str] = {}
    queue: list[tuple[float, str]] = [(0.0, origin)]
    settled: set[str] = set()

    while queue:
        cost, node = heapq.heappop(queue)
        if node in settled:
            continue
        settled.add(node)

        if node == destination:
            path = [node]
            while path[-1] != origin:
                path.append(previous[path[-1]])
            return list(reversed(path))

        for edge in neighbours(node):
            target = edge.to_code
            if target in settled:
                continue
            weight = edge.nominal_duration_hours * penalties.get((node, target), 1.0)
            candidate = cost + weight
            if candidate < best.get(target, float("inf")):
                best[target] = candidate
                previous[target] = node
                heapq.heappush(queue, (candidate, target))

    return None


def _edge_between(a: str, b: str) -> RoadEdge:
    for edge in neighbours(a):
        if edge.to_code == b:
            return edge
    raise RoutingError(f"Aucun axe routier entre {_name(a)} et {_name(b)}.")


def _build(path: list[str], *, route_id: str, label_fr: str) -> RouteGeometry:
    segments: list[RouteSegment] = []
    for a, b in pairwise(path):
        edge = _edge_between(a, b)
        node_a, node_b = NODES[a], NODES[b]
        segments.append(
            RouteSegment(
                from_code=a,
                from_name_fr=node_a.name_fr,
                from_latitude=node_a.coordinates.latitude,
                from_longitude=node_a.coordinates.longitude,
                to_code=b,
                to_name_fr=node_b.name_fr,
                to_latitude=node_b.coordinates.latitude,
                to_longitude=node_b.coordinates.longitude,
                distance_km=edge.distance_km,
                duration_hours=round(edge.nominal_duration_hours, 3),
                road_ref=edge.road_ref,
                road_class=edge.road_class,
                reliability=edge.reliability,
                notes_fr=edge.notes_fr,
            )
        )
    return RouteGeometry(
        id=route_id,
        label_fr=label_fr,
        node_codes=tuple(path),
        segments=tuple(segments),
        total_distance_km=round(sum(s.distance_km for s in segments), 1),
        total_duration_hours=round(sum(s.duration_hours for s in segments), 2),
    )


def _is_credible(candidate: RouteGeometry, accepted: list[RouteGeometry]) -> bool:
    """Praticable, et réellement différente de ce qui est déjà proposé."""
    if not accepted:
        return True

    best_duration = min(r.total_duration_hours for r in accepted)
    if candidate.total_duration_hours > best_duration * MAX_DURATION_RATIO:
        return False

    candidate_nodes = set(candidate.node_codes[1:-1])
    for existing in accepted:
        existing_nodes = set(existing.node_codes[1:-1])
        union = candidate_nodes | existing_nodes
        if union and len(candidate_nodes & existing_nodes) / len(union) > MAX_NODE_OVERLAP:
            return False
    return True


def _name(code: str) -> str:
    node = NODES.get(code)
    return node.name_fr if node else code


def _label(path: list[str], rank: int) -> str:
    """Nomme l'itinéraire par ses villes marquantes.

    « Itinéraire 2 » n'apprend rien à un exploitant ; « via Essaouira et Safi »
    lui fait reconnaître le corridor immédiatement.
    """
    prefix = "Itinéraire actuel" if rank == 0 else f"Alternative {rank}"
    intermediate = [NODES[c].name_fr for c in path[1:-1] if NODES[c].is_logistics_hub]
    if not intermediate:
        intermediate = [NODES[c].name_fr for c in path[1:-1]][:2]
    if intermediate:
        return f"{prefix} — via {', '.join(intermediate[:3])}"
    return f"{prefix} — {NODES[path[0]].name_fr} → {NODES[path[-1]].name_fr}"


#: Taille du graphe chargé, pour un diagnostic d'exploitation.
NETWORK_SIZE: dict[str, int] = {"nodes": len(NODES), "edges": len(EDGES)}
