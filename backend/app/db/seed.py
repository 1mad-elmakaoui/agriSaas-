"""Alimentation du référentiel et du jeu de démonstration.

Deux opérations, deux niveaux de privilège, et la distinction est structurelle.

**Le référentiel** (`crops`, `crop_growth_stages`, `soil_profiles`,
`irrigation_systems`) est global : ses lignes portent `tenant_id IS NULL`. La
politique d'isolation interdit à une organisation d'en écrire une ; seul le rôle
`atlas_owner` le peut, par une politique déclarée. C'est donc une opération
d'administration, au même titre qu'une migration.

**Le jeu de démonstration** est de la donnée d'organisation ordinaire, écrite
par le rôle applicatif sous politique, comme n'importe quelle saisie.

Rien de ce qui est chargé ici n'est une mesure. Chaque valeur agronomique porte
la table FAO dont elle vient ; chaque valeur de démonstration porte
`(SIMULATED, SEED_DEMO)` et pèse 0,30 dans le score de fiabilité, ce qui fait
qu'un tenant de démonstration ne peut pas afficher « Fiabilité : Élevée ».
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.base import (
    Crop,
    CropGrowthStage,
    Field,
    IrrigationSystem,
    Product,
    Region,
    Shipment,
    Site,
    SoilMoistureReading,
    SoilProfile,
    Tenant,
)
from app.domain.enums import (
    CropCategory,
    DataOrigin,
    DataState,
    GrowthStage,
    ShipmentStatus,
    SiteType,
    TransportMode,
)
from app.domain.provenance import SOURCE_SEED_DEMO

logger = get_logger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

__all__ = ["DEMO_TENANT_ID", "DEMO_TENANT_SLUG", "seed_demo_tenant", "seed_reference"]


def _load(name: str) -> dict[str, Any]:
    with (DATA_DIR / name).open(encoding="utf-8") as handle:
        payload: dict[str, Any] = json.load(handle)
    return payload


# ---------------------------------------------------------------------------
# Référentiel
# ---------------------------------------------------------------------------
async def seed_reference(session: AsyncSession) -> dict[str, int]:
    """Charge le référentiel global. Idempotent : réexécuter ne duplique rien.

    À exécuter avec une session d'administration ayant pris `atlas_owner` : les
    lignes globales sont hors de portée de la politique d'organisation, par
    conception.
    """
    counts: dict[str, int] = {}

    regions = _load("regions.json")["regions"]
    existing_regions = set(
        (await session.execute(select(Region.code))).scalars()
    )
    for row in regions:
        if row["code"] not in existing_regions:
            session.add(Region(**row))
    counts["regions"] = len(regions)

    soils = _load("soils.json")["soils"]
    existing_soils = set(
        (
            await session.execute(
                select(SoilProfile.code).where(SoilProfile.tenant_id.is_(None))
            )
        ).scalars()
    )
    for row in soils:
        if row["code"] not in existing_soils:
            session.add(SoilProfile(id=uuid.uuid4(), tenant_id=None, **row))
    counts["soil_profiles"] = len(soils)

    systems = _load("irrigation_systems.json")["systems"]
    existing_systems = set(
        (
            await session.execute(
                select(IrrigationSystem.code).where(IrrigationSystem.tenant_id.is_(None))
            )
        ).scalars()
    )
    for row in systems:
        if row["code"] not in existing_systems:
            session.add(IrrigationSystem(id=uuid.uuid4(), tenant_id=None, **row))
    counts["irrigation_systems"] = len(systems)

    crop_payload = _load("crops.json")
    existing_crops = {
        code: crop_id
        for code, crop_id in (
            await session.execute(
                select(Crop.code, Crop.id).where(Crop.tenant_id.is_(None))
            )
        ).all()
    }
    crop_ids: dict[str, uuid.UUID] = dict(existing_crops)
    for row in crop_payload["crops"]:
        if row["code"] in crop_ids:
            continue
        crop_id = uuid.uuid4()
        crop_ids[row["code"]] = crop_id
        session.add(
            Crop(
                id=crop_id,
                tenant_id=None,
                **{**row, "category": CropCategory(row["category"])},
            )
        )
    counts["crops"] = len(crop_payload["crops"])

    await session.flush()

    existing_stages = {
        (crop_id, stage)
        for crop_id, stage in (
            await session.execute(
                select(CropGrowthStage.crop_id, CropGrowthStage.stage)
            )
        ).all()
    }
    added = 0
    for row in crop_payload["stages"]:
        crop_id = crop_ids[row["crop_code"]]
        stage = GrowthStage(row["stage"])
        if (crop_id, stage) in existing_stages:
            continue
        session.add(
            CropGrowthStage(
                id=uuid.uuid4(),
                tenant_id=None,
                crop_id=crop_id,
                stage=stage,
                sequence=row["sequence"],
                length_days=row["length_days"],
                kc=row["kc"],
                source=row["source"],
            )
        )
        added += 1
    counts["crop_growth_stages"] = len(crop_payload["stages"])

    logger.info("reference_seeded", **counts)
    return counts


# ---------------------------------------------------------------------------
# Démonstration
# ---------------------------------------------------------------------------
DEMO_TENANT_SLUG = "souss-primeurs"

#: Le scénario de la §12 en une structure.
#:
#: Les parcelles et l'expédition appartiennent à **la même** organisation, et
#: EXP-1842 pointe sur la parcelle qui produit ses tomates. C'est ce lien qui
#: rend la démonstration cohérente : sans lui, on juxtapose deux jeux de données
#: voisins et le produit ne raconte rien.
_DEMO_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "code": "P01", "name_fr": "Serre tomate Nord", "area_ha": 3.5,
        "crop": "TOMATO", "soil": "SANDY_LOAM", "system": "DRIP",
        "latitude": 30.421, "longitude": -9.213, "elevation_m": 45.0,
        "distance_to_coast_km": 12.0, "flow_rate_m3_per_hour": 90.0,
        "planting_offset_days": -62, "moisture_pct": 24.0,
    },
    {
        "code": "P02", "name_fr": "Oliveraie Sud", "area_ha": 5.2,
        "crop": "OLIVE", "soil": "CLAY_LOAM", "system": "MICRO_SPRINKLER",
        "latitude": 30.409, "longitude": -9.198, "elevation_m": 52.0,
        "distance_to_coast_km": 14.0, "flow_rate_m3_per_hour": 130.0,
        "planting_offset_days": None, "moisture_pct": 19.0,
    },
    {
        "code": "P03", "name_fr": "Verger agrumes Est", "area_ha": 4.1,
        "crop": "CITRUS", "soil": "LOAM", "system": "DRIP",
        "latitude": 30.433, "longitude": -9.176, "elevation_m": 61.0,
        "distance_to_coast_km": 17.0, "flow_rate_m3_per_hour": 145.0,
        "planting_offset_days": None, "moisture_pct": 16.5,
    },
    {
        "code": "P04", "name_fr": "Melon primeur", "area_ha": 2.8,
        "crop": "MELON", "soil": "SANDY_LOAM", "system": "DRIP",
        "latitude": 30.402, "longitude": -9.230, "elevation_m": 38.0,
        "distance_to_coast_km": 9.0, "flow_rate_m3_per_hour": 70.0,
        "planting_offset_days": -35, "moisture_pct": 22.0,
    },
    {
        "code": "P05", "name_fr": "Poivron sous abri", "area_ha": 1.9,
        "crop": "PEPPER", "soil": "LOAM", "system": "DRIP",
        "latitude": 30.415, "longitude": -9.241, "elevation_m": 41.0,
        "distance_to_coast_km": 8.0,
        # Débit non renseigné, **volontairement** : la démonstration doit
        # montrer une parcelle où le produit répond « durée non calculable,
        # débit non renseigné » au lieu d'inventer une durée.
        "flow_rate_m3_per_hour": None,
        "planting_offset_days": -48, "moisture_pct": 27.0,
    },
)

_GHARB_FIELDS: tuple[dict[str, Any], ...] = (
    {
        "code": "G01", "name_fr": "Blé tendre Gharb", "area_ha": 22.6,
        "crop": "WHEAT", "soil": "SILT_LOAM", "system": "PIVOT",
        "latitude": 34.261, "longitude": -6.583, "elevation_m": 12.0,
        "distance_to_coast_km": 28.0, "flow_rate_m3_per_hour": 180.0,
        "planting_offset_days": -120, "moisture_pct": 31.0,
    },
    {
        "code": "G02", "name_fr": "Fraise Loukkos", "area_ha": 2.2,
        "crop": "STRAWBERRY", "soil": "LOAMY_SAND", "system": "DRIP",
        "latitude": 34.878, "longitude": -6.291, "elevation_m": 8.0,
        "distance_to_coast_km": 6.0, "flow_rate_m3_per_hour": 55.0,
        "planting_offset_days": -95, "moisture_pct": 26.0,
    },
)


async def seed_demo_tenant(session: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    """Peuple une organisation de démonstration cohérente.

    Appelée avec une session **déjà liée** à l'organisation : toutes les
    écritures passent sous politique, comme une saisie ordinaire. Rien ici n'est
    une mesure — chaque valeur porte `(SIMULATED, SEED_DEMO)`.
    """
    crops = {
        code: crop_id
        for code, crop_id in (
            await session.execute(
                select(Crop.code, Crop.id).where(Crop.tenant_id.is_(None))
            )
        ).all()
    }
    soils = {
        code: soil_id
        for code, soil_id in (
            await session.execute(
                select(SoilProfile.code, SoilProfile.id).where(
                    SoilProfile.tenant_id.is_(None)
                )
            )
        ).all()
    }
    systems = {
        code: system_id
        for code, system_id in (
            await session.execute(
                select(IrrigationSystem.code, IrrigationSystem.id).where(
                    IrrigationSystem.tenant_id.is_(None)
                )
            )
        ).all()
    }
    if not crops or not soils or not systems:
        raise RuntimeError(
            "Le référentiel global est vide : exécuter `seed-reference` avant "
            "`seed-demo`. Semer une démonstration sans référentiel produirait des "
            "parcelles sans culture ni sol, donc aucune recommandation."
        )

    today = datetime.now(UTC).date()
    counts: dict[str, int] = {}

    def _site(code: str, name: str, kind: SiteType, lat: float, lon: float,
              **extra: Any) -> Site:
        return Site(
            id=uuid.uuid4(), tenant_id=tenant_id, code=code, name_fr=name,
            site_type=kind, latitude=lat, longitude=lon,
            data_state=DataState.SIMULATED, data_origin=DataOrigin.SEED_DEMO,
            source_id=SOURCE_SEED_DEMO.id, **extra,
        )

    farm_souss = _site("F-SOUSS", "Domaine Souss Verde", SiteType.FARM, 30.42, -9.21,
                       region_code="SOUSS_MASSA")
    farm_gharb = _site("F-GHARB", "Domaine Gharb Nord", SiteType.FARM, 34.26, -6.58,
                       region_code="RABAT_SALE_KENITRA")
    hub_agadir = _site("E-AGADIR", "Plateforme Agadir", SiteType.HUB, 30.43, -9.60,
                       region_code="SOUSS_MASSA", capacity_tonnes=1200.0,
                       has_cold_storage=True)
    client_casa = _site("C-CASA", "Client Casablanca", SiteType.CUSTOMER, 33.57, -7.59,
                        region_code="CASABLANCA_SETTAT", capacity_tonnes=400.0,
                        has_cold_storage=True)
    for site in (farm_souss, farm_gharb, hub_agadir, client_casa):
        session.add(site)
    await session.flush()
    counts["sites"] = 4

    fields: dict[str, Field] = {}
    for spec, farm in (
        *((s, farm_souss) for s in _DEMO_FIELDS),
        *((s, farm_gharb) for s in _GHARB_FIELDS),
    ):
        planting = (
            today + timedelta(days=spec["planting_offset_days"])
            if spec["planting_offset_days"] is not None
            else None
        )
        field = Field(
            id=uuid.uuid4(), tenant_id=tenant_id, site_id=farm.id,
            code=spec["code"], name_fr=spec["name_fr"], area_ha=spec["area_ha"],
            latitude=spec["latitude"], longitude=spec["longitude"],
            elevation_m=spec["elevation_m"],
            distance_to_coast_km=spec["distance_to_coast_km"],
            crop_id=crops[spec["crop"]], soil_profile_id=soils[spec["soil"]],
            irrigation_system_id=systems[spec["system"]],
            planting_date=planting,
            flow_rate_m3_per_hour=spec["flow_rate_m3_per_hour"],
            water_cost_per_m3=1.8 if farm is farm_souss else 1.1,
        )
        session.add(field)
        fields[spec["code"]] = field
    await session.flush()
    counts["fields"] = len(fields)

    # Une mesure d'humidité par parcelle. `(SIMULATED, SEED_DEMO)` — pas
    # `(OBSERVED, MANUAL_ENTRY)` : personne n'a relevé ces valeurs, et le
    # produit existe pour que la différence reste visible.
    recorded = datetime.now(UTC) - timedelta(hours=6)
    for spec in (*_DEMO_FIELDS, *_GHARB_FIELDS):
        session.add(
            SoilMoistureReading(
                id=uuid.uuid4(), tenant_id=tenant_id,
                field_id=fields[spec["code"]].id,
                value_pct=spec["moisture_pct"], depth_cm=30.0,
                recorded_at=recorded,
                data_state=DataState.SIMULATED, data_origin=DataOrigin.SEED_DEMO,
                source_id=SOURCE_SEED_DEMO.id,
                note_fr="Valeur de démonstration : aucune mesure de terrain.",
            )
        )
    counts["soil_moisture_readings"] = len(fields)

    tomato = Product(
        id=uuid.uuid4(), tenant_id=tenant_id, code="P-TOM-EXP",
        name_fr="Tomate cerise export", crop_id=crops["TOMATO"],
        unit_price_mad_per_tonne=8200.0,
    )
    session.add(tomato)
    await session.flush()
    counts["products"] = 1

    departure = datetime.now(UTC) + timedelta(hours=14)
    session.add(
        Shipment(
            id=uuid.uuid4(), tenant_id=tenant_id, reference="EXP-1842",
            product_id=tomato.id,
            origin_site_id=hub_agadir.id, destination_site_id=client_casa.id,
            # Le lien qui rend la démonstration cohérente : ces tomates sortent
            # de la parcelle que le moteur d'irrigation conseille.
            source_field_id=fields["P01"].id,
            volume_tonnes=180.0,
            transport_mode=TransportMode.ROAD_REFRIGERATED,
            status=ShipmentStatus.PLANNED,
            optimization_profile="PERISSABLE_FROID",
            departure_at=departure,
            sla_deadline_at=departure + timedelta(hours=20),
        )
    )
    counts["shipments"] = 1

    logger.info("demo_seeded", tenant_id=str(tenant_id), **counts)
    return counts


#: Identifiant **fixe** de l'organisation de démonstration.
#:
#: Fixe et non tiré au hasard, pour une raison d'isolation et non de commodité :
#: vérifier l'existence d'une organisation par son `slug` exigerait de lire
#: `app.tenants` sans savoir laquelle on cherche, c'est-à-dire une lecture
#: inter-organisations — donc d'élargir une politique pour un besoin
#: d'amorçage. Avec un identifiant connu, la vérification se fait *à
#: l'intérieur* de la politique : on se déclare cette organisation, et on lit sa
#: seule ligne. Aucune politique n'est touchée.
DEMO_TENANT_ID = uuid.UUID("00000000-0000-4000-8000-0000000d3000")


async def ensure_demo_tenant(session: AsyncSession) -> uuid.UUID:
    """Crée l'organisation de démonstration si elle manque, et la marque comme telle."""
    tenant_id = DEMO_TENANT_ID
    await session.execute(
        text("SELECT set_config('app.current_tenant', :t, true)"),
        {"t": str(tenant_id)},
    )
    existing = (
        await session.execute(select(Tenant.id).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    session.add(
        Tenant(
            id=tenant_id, name="Souss Primeurs (démonstration)",
            slug=DEMO_TENANT_SLUG, region_code="SOUSS_MASSA",
            # Porté par le schéma, donc par l'API, donc par le bandeau de
            # l'interface : une donnée fabriquée ne doit pas pouvoir être prise
            # pour une mesure, même par quelqu'un de pressé.
            is_demo=True,
        )
    )
    await session.flush()
    return tenant_id
