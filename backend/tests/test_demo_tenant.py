"""Le jeu de démonstration : cohérent, et impossible à prendre pour des mesures.

Une démonstration qui se présente comme des données réelles est le défaut que ce
produit existe pour empêcher — au moment précis, la réunion commerciale, où
personne ne relit les étiquettes.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text

from app.db.base import Crop, Field, Shipment, SoilMoistureReading, Tenant
from app.db.seed import DEMO_TENANT_ID, DEMO_TENANT_SLUG, seed_demo_tenant
from app.db.session import Databases
from app.domain.enums import DataOrigin, DataState
from app.domain.provenance import reliability_level, reliability_score

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés, et un marqueur
# explicite ferait avertir pytest sur les tests synchrones du même module.


@pytest.fixture
async def demo(databases: Databases):
    """Sème l'organisation de démonstration dans une organisation jetable.

    Un identifiant neuf plutôt que `DEMO_TENANT_ID` : la suite ne doit pas
    dépendre de l'état laissé par la commande d'exploitation, ni le corrompre.
    """
    tenant_id = uuid.uuid4()
    owner_dsn = __import__("os").environ["ATLAS_TEST_OWNER_DATABASE_URL"]
    from sqlalchemy.ext.asyncio import create_async_engine

    owner = create_async_engine(owner_dsn)
    async with owner.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"), {"t": str(tenant_id)}
        )
        await conn.execute(
            text(
                "INSERT INTO app.tenants (id, name, slug, is_demo) "
                "VALUES (:id, 'Démo test', :slug, true)"
            ),
            {"id": tenant_id, "slug": f"demo-test-{tenant_id.hex[:8]}"},
        )
    async with databases.for_tenant(tenant_id).begin() as session:
        await seed_demo_tenant(session, tenant_id)
    try:
        yield tenant_id
    finally:
        async with owner.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": str(tenant_id)},
            )
            await conn.execute(text("DELETE FROM app.tenants WHERE id = :t"), {"t": tenant_id})
        await owner.dispose()


async def test_every_demo_value_is_flagged_simulated(
    databases: Databases, demo: uuid.UUID
) -> None:
    """Aucune valeur de démonstration ne se présente comme observée.

    C'est l'invariant central du jeu : la même colonne porte 24 % qu'il vienne
    d'une sonde ou d'un fichier, et seule la paire de provenance les distingue.
    """
    async with databases.for_tenant(demo).begin() as session:
        readings = (await session.execute(select(SoilMoistureReading))).scalars().all()
    assert readings
    for reading in readings:
        assert reading.data_state is DataState.SIMULATED
        assert reading.data_origin is DataOrigin.SEED_DEMO


async def test_the_demo_tenant_cannot_report_high_reliability(
    databases: Databases, demo: uuid.UUID
) -> None:
    """Un jeu fabriqué doit afficher « Fiabilité : Faible ».

    Le poids de `SEED_DEMO` (0,30) est ce qui l'impose. Si quelqu'un le relevait
    pour « faire meilleure impression en démonstration », ce test tomberait.
    """
    async with databases.for_tenant(demo).begin() as session:
        origins = list(
            (await session.execute(select(SoilMoistureReading.data_origin))).scalars()
        )
    level = reliability_level(reliability_score(origins))
    assert level is not None
    assert level.label_fr == "Faible"


async def test_the_tenant_is_marked_as_a_demonstration(
    databases: Databases, demo: uuid.UUID
) -> None:
    """Le drapeau vit dans le schéma, donc il traverse l'API jusqu'au bandeau.

    Le déduire du nom de l'organisation le rendrait cassable par un renommage.
    """
    async with databases.for_tenant(demo).begin() as session:
        is_demo = (
            await session.execute(select(Tenant.is_demo).where(Tenant.id == demo))
        ).scalar_one()
    assert is_demo is True


async def test_the_demonstration_is_one_connected_story(
    databases: Databases, demo: uuid.UUID
) -> None:
    """L'expédition part d'une parcelle qui existe et qui produit son produit.

    Sans ce lien, on juxtapose deux jeux de données voisins et la démonstration
    ne raconte rien : la question « faut-il irriguer P01 ? » et la question
    « EXP-1842 est-elle à risque ? » ne parleraient pas du même exploitant.
    """
    async with databases.for_tenant(demo).begin() as session:
        shipment = (
            await session.execute(
                select(Shipment).where(Shipment.reference == "EXP-1842")
            )
        ).scalar_one()
        assert shipment.source_field_id is not None

        field = (
            await session.execute(
                select(Field).where(Field.id == shipment.source_field_id)
            )
        ).scalar_one()
        crop = (
            await session.execute(select(Crop).where(Crop.id == field.crop_id))
        ).scalar_one()

    assert field.code == "P01"
    assert crop.code == "TOMATO"
    # Le produit exige la chaîne du froid, ce qui est ce qui rendra un entrepôt
    # sans froid infaisable en phase 3.
    assert crop.requires_cold_chain is True


async def test_the_demo_includes_a_field_without_a_flow_rate(
    databases: Databases, demo: uuid.UUID
) -> None:
    """Une parcelle sans débit, exprès.

    Le produit doit répondre « durée non calculable, débit non renseigné »
    quelque part dans la démonstration. Si toutes les parcelles avaient un débit,
    le chemin « ce qui manque manque » ne serait jamais montré — et c'est
    précisément ce qui distingue ce produit d'un tableau de bord qui invente.
    """
    async with databases.for_tenant(demo).begin() as session:
        without = (
            await session.execute(
                select(func.count())
                .select_from(Field)
                .where(Field.flow_rate_m3_per_hour.is_(None))
            )
        ).scalar_one()
    assert without >= 1


async def test_every_demo_field_resolves_its_agronomy(
    databases: Databases, demo: uuid.UUID
) -> None:
    """Chaque parcelle a une culture, un sol et un système.

    Une parcelle sans l'un des trois ne produit aucune recommandation : la
    démonstration s'arrêterait sur un écran vide, ce qui est pire qu'un chiffre
    manquant parce que rien n'en explique la cause.
    """
    async with databases.for_tenant(demo).begin() as session:
        fields = (await session.execute(select(Field))).scalars().all()
    assert len(fields) >= 5
    for field in fields:
        assert field.crop_id is not None, field.code
        assert field.soil_profile_id is not None, field.code
        assert field.irrigation_system_id is not None, field.code


async def test_the_demo_spans_two_regions(databases: Databases, demo: uuid.UUID) -> None:
    """Souss-Massa et Gharb : deux climats, deux calendriers, deux cultures.

    Un jeu mono-région laisserait passer toute erreur liée à la latitude, au
    rayonnement ou au calendrier cultural.
    """
    async with databases.for_tenant(demo).begin() as session:
        latitudes = list((await session.execute(select(Field.latitude))).scalars())
    assert max(latitudes) - min(latitudes) > 3.0, latitudes


def test_the_demo_tenant_identifier_is_fixed() -> None:
    """Identifiant connu : la vérification d'existence reste sous politique.

    Chercher l'organisation par son `slug` exigerait une lecture
    inter-organisations, donc d'élargir une politique pour un besoin d'amorçage.
    """
    assert str(DEMO_TENANT_ID).startswith("00000000-")
    assert DEMO_TENANT_SLUG == "souss-primeurs"
