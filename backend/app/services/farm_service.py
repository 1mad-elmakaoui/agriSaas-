"""Lectures d'exploitation : parcelles et expéditions, pour l'interface.

Ce module ne calcule rien. Il rassemble ce que l'écran doit montrer avant même
qu'une recommandation soit demandée : quelles parcelles existent, ce qu'on sait
d'elles, et ce qui empêcherait une recommandation d'aboutir.

Ce dernier point est délibéré. Une parcelle sans culture renseignée ne peut pas
recevoir de recommandation ; l'interface doit pouvoir l'afficher **en gris avec
la raison** plutôt que de la faire disparaître de la liste. Une parcelle absente
d'un écran est indiscernable d'une parcelle qui va bien.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.base import (
    Crop,
    Field,
    IrrigationSystem,
    Product,
    Shipment,
    Site,
    SoilMoistureReading,
    SoilProfile,
)
from app.domain.enums import DataOrigin, DataState

__all__ = ["FarmService", "FieldSummary", "ShipmentSummary"]


@dataclass(frozen=True, slots=True)
class FieldSummary:
    """Ce qu'une carte de parcelle affiche sans avoir à lancer le moteur."""

    code: str
    name_fr: str
    site_name_fr: str
    area_ha: float
    latitude: float
    longitude: float
    boundary_geojson: dict[str, object] | None
    crop_code: str | None
    crop_name_fr: str | None
    soil_name_fr: str | None
    system_name_fr: str | None
    has_flow_rate: bool
    has_water_tariff: bool
    moisture_pct: float | None
    moisture_recorded_at: datetime | None
    moisture_state: DataState | None
    moisture_origin: DataOrigin | None
    #: Renseigné quand aucune recommandation ne peut être produite. L'interface
    #: affiche la parcelle avec ce motif au lieu du chiffre absent.
    blocked_reason_fr: str | None


@dataclass(frozen=True, slots=True)
class ShipmentSummary:
    reference: str
    product_name_fr: str | None
    volume_tonnes: float
    transport_mode: str
    transport_mode_label_fr: str
    status: str
    status_label_fr: str
    origin_site_fr: str
    destination_site_fr: str
    origin_latitude: float
    origin_longitude: float
    destination_latitude: float
    destination_longitude: float
    departure_at: datetime
    sla_deadline_at: datetime
    requires_cold_chain: bool | None
    source_field_code: str | None


class FarmService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_fields(self) -> list[FieldSummary]:
        fields = list((await self._session.execute(select(Field))).scalars())
        sites = {s.id: s for s in (await self._session.execute(select(Site))).scalars()}
        crops = {c.id: c for c in (await self._session.execute(select(Crop))).scalars()}
        soils = {
            s.id: s for s in (await self._session.execute(select(SoilProfile))).scalars()
        }
        systems = {
            s.id: s
            for s in (await self._session.execute(select(IrrigationSystem))).scalars()
        }
        moistures = await self._latest_moisture_by_field()

        summaries = [
            self._summarise(field, sites, crops, soils, systems, moistures)
            for field in fields
        ]
        return sorted(summaries, key=lambda s: s.code)

    async def _latest_moisture_by_field(self) -> dict[uuid.UUID, SoilMoistureReading]:
        """La mesure la plus récente par parcelle.

        Triée en Python plutôt que par `DISTINCT ON` : le volume est celui d'une
        exploitation, et une requête lisible vaut mieux ici qu'une requête
        astucieuse que personne ne relira.
        """
        readings = list(
            (await self._session.execute(select(SoilMoistureReading))).scalars()
        )
        latest: dict[uuid.UUID, SoilMoistureReading] = {}
        for reading in sorted(readings, key=lambda r: r.recorded_at):
            latest[reading.field_id] = reading
        return latest

    @staticmethod
    def _summarise(
        field: Field,
        sites: dict[uuid.UUID, Site],
        crops: dict[uuid.UUID, Crop],
        soils: dict[uuid.UUID, SoilProfile],
        systems: dict[uuid.UUID, IrrigationSystem],
        moistures: dict[uuid.UUID, SoilMoistureReading],
    ) -> FieldSummary:
        crop = crops.get(field.crop_id) if field.crop_id else None
        soil = soils.get(field.soil_profile_id) if field.soil_profile_id else None
        system = (
            systems.get(field.irrigation_system_id)
            if field.irrigation_system_id
            else None
        )
        moisture = moistures.get(field.id)
        site = sites.get(field.site_id)

        # L'ordre suit celui du service d'irrigation : la première information
        # manquante est celle qui bloquera, et c'est celle qu'il faut nommer.
        blocked: str | None = None
        if crop is None:
            blocked = "Aucune culture renseignée pour cette parcelle."
        elif soil is None:
            blocked = "Aucun profil de sol renseigné pour cette parcelle."
        elif system is None:
            blocked = "Aucun système d'irrigation renseigné pour cette parcelle."
        elif moisture is None:
            blocked = "Aucune mesure d'humidité du sol pour cette parcelle."

        return FieldSummary(
            code=field.code,
            name_fr=field.name_fr,
            site_name_fr=site.name_fr if site else "Site inconnu",
            area_ha=field.area_ha,
            latitude=field.latitude,
            longitude=field.longitude,
            boundary_geojson=field.boundary_geojson,
            crop_code=crop.code if crop else None,
            crop_name_fr=crop.name_fr if crop else None,
            soil_name_fr=soil.name_fr if soil else None,
            system_name_fr=system.name_fr if system else None,
            has_flow_rate=field.flow_rate_m3_per_hour is not None,
            has_water_tariff=field.water_cost_per_m3 is not None,
            moisture_pct=moisture.value_pct if moisture else None,
            moisture_recorded_at=moisture.recorded_at if moisture else None,
            moisture_state=moisture.data_state if moisture else None,
            moisture_origin=moisture.data_origin if moisture else None,
            blocked_reason_fr=blocked,
        )

    # -- expéditions -------------------------------------------------------

    async def list_shipments(self) -> list[ShipmentSummary]:
        shipments = list((await self._session.execute(select(Shipment))).scalars())
        return [await self._shipment_summary(s) for s in
                sorted(shipments, key=lambda s: s.departure_at)]

    async def get_shipment(self, reference: str) -> ShipmentSummary:
        shipment = (
            await self._session.execute(
                select(Shipment).where(Shipment.reference == reference.upper())
            )
        ).scalar_one_or_none()
        if shipment is None:
            raise NotFoundError(
                f"Expédition « {reference} » introuvable.",
                remedy_fr="Vérifiez la référence dans la page Expéditions.",
            )
        return await self._shipment_summary(shipment)

    async def _shipment_summary(self, shipment: Shipment) -> ShipmentSummary:
        sites = {s.id: s for s in (await self._session.execute(select(Site))).scalars()}
        product_name: str | None = None
        cold_chain: bool | None = None
        if shipment.product_id is not None:
            product = (
                await self._session.execute(
                    select(Product).where(Product.id == shipment.product_id)
                )
            ).scalar_one_or_none()
            if product is not None:
                product_name = product.name_fr
                if product.crop_id is not None:
                    crop = (
                        await self._session.execute(
                            select(Crop).where(Crop.id == product.crop_id)
                        )
                    ).scalar_one_or_none()
                    cold_chain = crop.requires_cold_chain if crop else None

        source_field_code: str | None = None
        if shipment.source_field_id is not None:
            field = (
                await self._session.execute(
                    select(Field).where(Field.id == shipment.source_field_id)
                )
            ).scalar_one_or_none()
            source_field_code = field.code if field else None

        origin = sites[shipment.origin_site_id]
        destination = sites[shipment.destination_site_id]
        return ShipmentSummary(
            reference=shipment.reference,
            product_name_fr=product_name,
            volume_tonnes=shipment.volume_tonnes,
            transport_mode=shipment.transport_mode.value,
            transport_mode_label_fr=shipment.transport_mode.label_fr,
            status=shipment.status.value,
            status_label_fr=shipment.status.label_fr,
            origin_site_fr=origin.name_fr,
            destination_site_fr=destination.name_fr,
            origin_latitude=origin.latitude,
            origin_longitude=origin.longitude,
            destination_latitude=destination.latitude,
            destination_longitude=destination.longitude,
            departure_at=shipment.departure_at,
            sla_deadline_at=shipment.sla_deadline_at,
            requires_cold_chain=cold_chain,
            source_field_code=source_field_code,
        )

    @staticmethod
    def hours_until(moment: datetime) -> float:
        return (moment - datetime.now(UTC)).total_seconds() / 3600.0
