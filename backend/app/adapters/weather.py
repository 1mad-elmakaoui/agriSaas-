"""Fournisseurs météo.

Une seule abstraction, deux implémentations, et la différence est **visible dans
la donnée** plutôt que dans la configuration :

* `OpenMeteoProvider` — service réel, sans clé. Ce qu'il rend est `OBSERVED` ou
  `FORECAST`, d'origine `EXTERNAL_API`.
* `OfflineProvider` — série déterministe pour le développement et la
  démonstration. Tout ce qu'il rend est `SIMULATED` / `SEED_DEMO`, et le reste
  jusqu'à l'écran. Il ne s'agit pas d'un repli discret : une organisation servie
  par ce fournisseur voit « Simulé » sur chaque valeur et sa fiabilité tombe.

Le navigateur ne parle jamais à un fournisseur météo : tout passe par l'API.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

import httpx

from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger
from app.domain.enums import DataOrigin, DataState
from app.domain.provenance import DataSourceRef

logger = get_logger(__name__)

__all__ = [
    "DailyWeather",
    "OfflineProvider",
    "OpenMeteoProvider",
    "WeatherProvider",
    "build_weather_provider",
]

SOURCE_OPEN_METEO = DataSourceRef(
    id="open-meteo",
    label_fr="Open-Meteo",
    kind="weather",
    detail="Agrégat de modèles météorologiques nationaux, sans clé d'API",
)
SOURCE_OFFLINE = DataSourceRef(
    id="weather-offline",
    label_fr="Météo simulée (hors ligne)",
    kind="weather",
    detail="Série déterministe — jamais une observation",
)


@dataclass(frozen=True, slots=True)
class DailyWeather:
    """Agrégat journalier, tel que le moteur FAO-56 le consomme.

    RHmax et RHmin sont les extrema **horaires** de la journée, comme la FAO-56
    les définit. C'est la raison pour laquelle l'horaire est le grain de stockage
    et le journalier une vue dérivée.
    """

    day: date
    temp_max_c: float
    temp_min_c: float
    relative_humidity_max_pct: float | None
    relative_humidity_min_pct: float | None
    wind_speed_m_s: float | None
    wind_measurement_height_m: float
    solar_radiation_mj_m2_day: float | None
    precipitation_mm: float
    precipitation_probability_pct: float | None
    provider_et0_mm: float | None
    state: DataState
    origin: DataOrigin
    source: DataSourceRef

    @property
    def is_simulated(self) -> bool:
        return self.state is DataState.SIMULATED


@dataclass(frozen=True, slots=True)
class HourlyWeather:
    """Conditions attendues sur une heure, en un point.

    Séparé de `DailyWeather` parce que les deux répondent à des questions
    différentes. L'irrigation raisonne à la journée : c'est le cumul quotidien
    qui remplit une réserve utile. Le risque routier raisonne à l'heure :
    60 mm étalés sur deux jours ne coupent pas une route, 25 mm en une heure si.
    Les fusionner obligerait l'un des deux moteurs à comparer une grandeur à un
    seuil qui n'est pas le sien.
    """

    at: datetime
    precipitation_mm: float | None
    wind_gust_kmh: float | None
    temperature_c: float | None
    state: DataState
    origin: DataOrigin
    source: DataSourceRef


class WeatherProvider(Protocol):
    name: str

    async def daily(
        self, latitude: float, longitude: float, *, days: int
    ) -> list[DailyWeather]: ...

    async def hourly(
        self, latitude: float, longitude: float, *, hours: int
    ) -> list[HourlyWeather]: ...


class OpenMeteoProvider:
    """Adaptateur Open-Meteo.

    **Aller-retour réseau jamais exécuté dans cet environnement** : le proxy de
    développement refuse les hôtes tiers. Le code est écrit, il n'est pas
    vérifié — ligne du registre d'honnêteté.
    """

    name = "open-meteo"
    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    #: Demandées dans l'ordre du contrat interne. `et0_fao_evapotranspiration`
    #: est conservée comme **contrôle croisé** de notre Penman-Monteith, jamais
    #: affichée comme une ET0 concurrente.
    DAILY_VARIABLES = (
        "temperature_2m_max",
        "temperature_2m_min",
        "relative_humidity_2m_max",
        "relative_humidity_2m_min",
        "wind_speed_10m_max",
        "shortwave_radiation_sum",
        "precipitation_sum",
        "precipitation_probability_max",
        "et0_fao_evapotranspiration",
    )

    #: Variables horaires du risque routier. `wind_gusts_10m` et non
    #: `wind_speed_10m` : c'est la rafale qui renverse un semi-remorque, pas le
    #: vent moyen.
    HOURLY_VARIABLES = (
        "precipitation",
        "wind_gusts_10m",
        "temperature_2m",
    )

    def __init__(self, timeout_s: float = 10.0) -> None:
        self._timeout = timeout_s

    async def daily(
        self, latitude: float, longitude: float, *, days: int
    ) -> list[DailyWeather]:
        params = {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "daily": ",".join(self.DAILY_VARIABLES),
            "forecast_days": str(days),
            "timezone": "UTC",
            "wind_speed_unit": "ms",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self.BASE_URL, params=params)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Dégradation explicite : on nomme le correctif, on ne substitue
            # aucune valeur. Sans météo, il n'y a pas de recommandation.
            raise ProviderUnavailableError(
                "Le service météo Open-Meteo est momentanément injoignable.",
                remedy_fr=(
                    "Réessayez dans quelques minutes. Aucune valeur n'a été estimée "
                    "à la place."
                ),
            ) from exc

        return list(self._parse(payload))

    def _parse(self, payload: dict[str, object]) -> Iterator[DailyWeather]:
        daily = payload.get("daily")
        if not isinstance(daily, dict):
            raise ProviderUnavailableError(
                "Réponse météo inexploitable : bloc journalier absent."
            )
        times = daily.get("time") or []
        if not isinstance(times, list):
            raise ProviderUnavailableError("Réponse météo inexploitable.")

        def column(key: str) -> list[float | None]:
            values = daily.get(key)
            return list(values) if isinstance(values, list) else [None] * len(times)

        for index, day_str in enumerate(times):

            def at(key: str, *, index: int = index) -> float | None:
                # `index` lié par défaut. La fermeture n'est appelée que
                # dans son propre tour de boucle, donc le résultat est le même
                # aujourd'hui ; la lier ferme la porte au jour où quelqu'un
                # différera l'appel, cas où chaque jour recevrait la météo du
                # dernier sans que rien ne casse.
                values = column(key)
                value = values[index] if index < len(values) else None
                return float(value) if value is not None else None

            temp_max, temp_min = at("temperature_2m_max"), at("temperature_2m_min")
            if temp_max is None or temp_min is None:
                # Sans températures il n'y a pas d'ET0 possible : on saute le
                # jour plutôt que d'inventer une valeur.
                continue
            radiation_mj = at("shortwave_radiation_sum")
            yield DailyWeather(
                day=date.fromisoformat(str(day_str)),
                temp_max_c=temp_max,
                temp_min_c=temp_min,
                relative_humidity_max_pct=at("relative_humidity_2m_max"),
                relative_humidity_min_pct=at("relative_humidity_2m_min"),
                wind_speed_m_s=at("wind_speed_10m_max"),
                wind_measurement_height_m=10.0,
                solar_radiation_mj_m2_day=radiation_mj,
                precipitation_mm=at("precipitation_sum") or 0.0,
                precipitation_probability_pct=at("precipitation_probability_max"),
                provider_et0_mm=at("et0_fao_evapotranspiration"),
                state=DataState.FORECAST,
                origin=DataOrigin.EXTERNAL_API,
                source=SOURCE_OPEN_METEO,
            )


    async def hourly(
        self, latitude: float, longitude: float, *, hours: int
    ) -> list[HourlyWeather]:
        """Série horaire, pour l'exposition d'un itinéraire.

        Comme `daily`, **jamais exécutée dans cet environnement**.
        """
        params = {
            "latitude": f"{latitude:.4f}",
            "longitude": f"{longitude:.4f}",
            "hourly": ",".join(self.HOURLY_VARIABLES),
            "forecast_hours": str(hours),
            "past_hours": "24",
            "timezone": "UTC",
            "wind_speed_unit": "kmh",
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(self.BASE_URL, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("open_meteo_hourly_failed", error=str(exc))
            raise ProviderUnavailableError(
                "Le service météo Open-Meteo est momentanément injoignable.",
                remedy_fr=(
                    "Réessayez dans quelques minutes. Aucune condition n'a été "
                    "estimée à la place."
                ),
            ) from exc
        return list(self._parse_hourly(payload))

    def _parse_hourly(self, payload: dict[str, object]) -> Iterator[HourlyWeather]:
        block = payload.get("hourly")
        if not isinstance(block, dict):
            raise ProviderUnavailableError(
                "Réponse météo inexploitable : bloc horaire absent."
            )
        times = block.get("time") or []
        if not isinstance(times, list):
            raise ProviderUnavailableError("Réponse météo inexploitable.")

        def column(key: str) -> list[float | None]:
            values = block.get(key)
            return list(values) if isinstance(values, list) else [None] * len(times)

        for index, moment in enumerate(times):

            def at(key: str, *, index: int = index) -> float | None:
                values = column(key)
                value = values[index] if index < len(values) else None
                return float(value) if value is not None else None

            yield HourlyWeather(
                at=datetime.fromisoformat(str(moment)).replace(tzinfo=UTC),
                precipitation_mm=at("precipitation"),
                wind_gust_kmh=at("wind_gusts_10m"),
                temperature_c=at("temperature_2m"),
                state=DataState.FORECAST,
                origin=DataOrigin.EXTERNAL_API,
                source=SOURCE_OPEN_METEO,
            )


class OfflineProvider:
    """Série météo déterministe, entièrement simulée.

    Déterministe et non aléatoire : la même parcelle au même jour donne la même
    recommandation, ce qui rend la démonstration reproductible et les tests
    stables. Les valeurs suivent une saisonnalité grossière autour de la
    latitude — assez pour que le moteur travaille, jamais assez pour ressembler
    à une prévision.
    """

    name = "offline"

    async def daily(
        self, latitude: float, longitude: float, *, days: int
    ) -> list[DailyWeather]:
        today = datetime.now(UTC).date()
        out: list[DailyWeather] = []
        for offset in range(days):
            day = today + timedelta(days=offset)
            # Saisonnalité sinusoïdale simple, décalée par la latitude.
            seasonal = math.sin((day.timetuple().tm_yday / 365.0) * 2 * math.pi - 1.4)
            base = 26.0 + 6.0 * seasonal - (latitude - 30.0) * 0.6
            amplitude = 9.0 + 2.0 * math.cos(longitude)
            out.append(
                DailyWeather(
                    day=day,
                    temp_max_c=round(base + amplitude / 2, 1),
                    temp_min_c=round(base - amplitude / 2, 1),
                    relative_humidity_max_pct=72.0,
                    relative_humidity_min_pct=34.0,
                    wind_speed_m_s=2.4,
                    wind_measurement_height_m=10.0,
                    solar_radiation_mj_m2_day=None,
                    precipitation_mm=0.0,
                    precipitation_probability_pct=5.0,
                    provider_et0_mm=None,
                    # Le point du fournisseur hors ligne : tout ce qu'il rend est
                    # marqué simulé, jusqu'à la puce de source dans l'interface.
                    state=DataState.SIMULATED,
                    origin=DataOrigin.SEED_DEMO,
                    source=SOURCE_OFFLINE,
                )
            )
        return out

    async def hourly(
        self, latitude: float, longitude: float, *, hours: int
    ) -> list[HourlyWeather]:
        """Série horaire déterministe, avec une perturbation reproductible.

        Une démonstration logistique où rien ne se passe ne démontre rien : le
        produit existe pour montrer ce qu'il faut faire **quand** quelque chose
        arrive. La perturbation est donc placée par une fonction de la position
        et de l'heure, pas au hasard — la même expédition donne toujours la même
        recommandation, et tout ce qui en sort est étiqueté « Simulé ».

        Elle vise le relief intérieur : la section de montagne de l'A7 est
        précisément l'endroit où un corridor rapide devient fragile, et c'est ce
        contraste que la démonstration doit rendre visible.
        """
        start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(
            hours=24
        )
        inland = longitude > -9.0 and 30.8 < latitude < 32.2
        out: list[HourlyWeather] = []
        for offset in range(hours + 24):
            at = start + timedelta(hours=offset)
            # Fenêtre de perturbation : de 6 h à 16 h après l'instant courant.
            in_window = 30 <= offset < 40
            disturbed = inland and in_window
            out.append(
                HourlyWeather(
                    at=at,
                    precipitation_mm=14.0 if disturbed else 0.0,
                    wind_gust_kmh=88.0 if disturbed else 18.0,
                    temperature_c=15.0 if disturbed else 24.0,
                    state=DataState.SIMULATED,
                    origin=DataOrigin.SEED_DEMO,
                    source=SOURCE_OFFLINE,
                )
            )
        return out


def build_weather_provider(name: str) -> WeatherProvider:
    if name == "offline":
        return OfflineProvider()
    return OpenMeteoProvider()
