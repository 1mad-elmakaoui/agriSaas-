"""Primitives géospatiales.

Volontairement minimales : distance orthodromique, interpolation le long d'un
tracé, et intersection d'un segment avec une zone circulaire. Ces trois
opérations couvrent tout ce dont le moteur d'exposition a besoin.

PostGIS est présent dans ce dépôt et porte les frontières de parcelles ; ces
fonctions restent en Python parce que le moteur d'exposition vit dans `domain/`,
qui n'ouvre aucune connexion. Une distance calculée en base et une distance
calculée ici doivent donner le même nombre — c'est pour cela qu'une seule des
deux est utilisée dans une décision, et c'est celle-ci.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0088


@dataclass(frozen=True)
class Coordinates:
    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90.0 <= self.latitude <= 90.0:
            raise ValueError(f"Latitude hors bornes : {self.latitude}")
        if not -180.0 <= self.longitude <= 180.0:
            raise ValueError(f"Longitude hors bornes : {self.longitude}")

    def as_lonlat(self) -> tuple[float, float]:
        """Ordre GeoJSON / MapLibre (longitude, latitude)."""
        return (self.longitude, self.latitude)


def haversine_km(a: Coordinates, b: Coordinates) -> float:
    """Distance orthodromique en kilomètres."""
    lat1, lon1 = math.radians(a.latitude), math.radians(a.longitude)
    lat2, lon2 = math.radians(b.latitude), math.radians(b.longitude)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def interpolate(a: Coordinates, b: Coordinates, ratio: float) -> Coordinates:
    """Point à `ratio` (0→1) le long du segment a→b.

    Interpolation linéaire en degrés : sur les distances marocaines (quelques
    centaines de kilomètres) l'écart avec une interpolation sphérique est
    négligeable devant l'incertitude des données d'exposition.
    """
    ratio = max(0.0, min(1.0, ratio))
    return Coordinates(
        latitude=a.latitude + (b.latitude - a.latitude) * ratio,
        longitude=a.longitude + (b.longitude - a.longitude) * ratio,
    )


def distance_point_to_segment_km(
    point: Coordinates, start: Coordinates, end: Coordinates
) -> float:
    """Distance la plus courte entre un point et un segment.

    Projection en plan local équirectangulaire centré sur le point : l'erreur
    reste inférieure au pour-cent aux latitudes marocaines, pour un coût de
    calcul négligeable.
    """
    lat_ref = math.radians(point.latitude)
    kx = EARTH_RADIUS_KM * math.cos(lat_ref) * math.pi / 180.0
    ky = EARTH_RADIUS_KM * math.pi / 180.0

    px, py = point.longitude * kx, point.latitude * ky
    ax, ay = start.longitude * kx, start.latitude * ky
    bx, by = end.longitude * kx, end.latitude * ky

    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        return math.hypot(px - ax, py - ay)

    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def segment_overlap_fraction(
    start: Coordinates, end: Coordinates, center: Coordinates, radius_km: float
) -> float:
    """Fraction du segment située dans un disque de rayon `radius_km`.

    Renvoie une valeur dans [0,1]. Utilisé pour mesurer quelle part d'un tronçon
    routier traverse une zone de risque, plutôt que de raisonner en tout ou rien :
    un tronçon effleuré et un tronçon entièrement traversé n'exposent pas de la
    même manière.

    L'échantillonnage est adaptatif (un point tous les ~2 km, borné) : suffisant
    pour une décision logistique, et déterministe donc testable.
    """
    if radius_km <= 0:
        return 0.0

    length_km = haversine_km(start, end)
    if length_km == 0.0:
        return 1.0 if haversine_km(start, center) <= radius_km else 0.0

    samples = max(8, min(200, int(length_km / 2.0) + 1))
    inside = sum(
        1
        for i in range(samples)
        if haversine_km(interpolate(start, end, (i + 0.5) / samples), center) <= radius_km
    )
    return inside / samples


def bounding_box(points: list[Coordinates]) -> tuple[float, float, float, float]:
    """Emprise (min_lon, min_lat, max_lon, max_lat) pour cadrer la carte."""
    if not points:
        raise ValueError("Emprise indéfinie : aucune coordonnée fournie")
    lons = [p.longitude for p in points]
    lats = [p.latitude for p in points]
    return (min(lons), min(lats), max(lons), max(lats))
