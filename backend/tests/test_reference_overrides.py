"""Surcharge du référentiel par organisation.

Le référentiel est global et **surchargeable**. Trois propriétés doivent tenir
ensemble, et c'est leur combinaison qui est délicate :

1. tout le monde lit la ligne FAO globale ;
2. une organisation peut poser la sienne, qui masque la globale pour elle seule ;
3. **personne ne peut réécrire la globale** par la voie applicative.

Perdre la troisième transformerait une calibration locale en modification du
référentiel pour tous les clients — sans que rien ne le signale.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.base import Crop, SoilProfile
from app.db.session import Databases
from app.domain.enums import CropCategory
from tests.conftest import Org

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés, et un marqueur
# explicite ferait avertir pytest sur les tests synchrones du même module.


async def test_the_global_reference_is_readable_by_every_organisation(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    """Sans cela, une organisation neuve n'aurait aucune agronomie."""
    alpha, beta = two_orgs
    for org in (alpha, beta):
        async with databases.for_tenant(org.tenant_id).begin() as session:
            codes = set(
                (
                    await session.execute(
                        select(Crop.code).where(Crop.tenant_id.is_(None))
                    )
                ).scalars()
            )
        assert {"TOMATO", "CITRUS", "OLIVE"} <= codes


async def test_an_organisation_cannot_write_a_global_reference_row(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    """Une ligne globale écrite depuis une organisation modifierait le
    référentiel de tous les clients. Le `WITH CHECK` l'interdit."""
    alpha, _ = two_orgs
    with pytest.raises(DBAPIError) as excinfo:
        async with databases.for_tenant(alpha.tenant_id).begin() as session:
            await session.execute(
                text(
                    "INSERT INTO app.soil_profiles "
                    "(id, tenant_id, code, name_fr, name_en, theta_fc_m3_m3, "
                    " theta_wp_m3_m3, hydraulic_source, is_measured) "
                    "VALUES (gen_random_uuid(), NULL, 'PIRATE', 'x', 'x', 0.3, 0.1, "
                    "'inventé', false)"
                )
            )
    assert "row-level security" in str(excinfo.value).lower()


async def test_an_organisation_cannot_modify_the_global_row(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    """Un UPDATE ne doit pas non plus atteindre la ligne FAO.

    La ligne est **lisible**, donc un `UPDATE ... WHERE tenant_id IS NULL` la
    trouve bien. C'est le `WITH CHECK` qui l'arrête, et il le fait en **levant**
    plutôt qu'en ne touchant aucune ligne : la tentative est bruyante, ce qui est
    le bon comportement pour une écriture qui aurait modifié le référentiel de
    tous les clients. Un `rowcount = 0` silencieux laisserait croire à un
    no-op anodin.

    Sans `WITH CHECK`, la lecture partagée serait aussi une écriture partagée.
    """
    alpha, _ = two_orgs
    with pytest.raises(DBAPIError) as excinfo:
        async with databases.for_tenant(alpha.tenant_id).begin() as session:
            await session.execute(
                text(
                    "UPDATE app.crops SET kc_mid = 9.99 "
                    "WHERE tenant_id IS NULL AND code = 'TOMATO'"
                )
            )
    assert "row-level security" in str(excinfo.value).lower()

    async with databases.for_tenant(alpha.tenant_id).begin() as session:
        kc = (
            await session.execute(
                select(Crop.kc_mid).where(
                    Crop.tenant_id.is_(None), Crop.code == "TOMATO"
                )
            )
        ).scalar_one()
    assert kc != 9.99


async def test_an_override_is_visible_to_its_owner_and_to_nobody_else(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    """La calibration locale reste une tâche de données.

    Une organisation qui fait analyser un sol pose sa propre ligne avec
    `is_measured = true`. Elle la voit ; l'autre organisation ne la voit pas ;
    la ligne FAO est intacte pour les deux.
    """
    alpha, beta = two_orgs
    async with databases.for_tenant(alpha.tenant_id).begin() as session:
        session.add(
            SoilProfile(
                id=uuid.uuid4(),
                tenant_id=alpha.tenant_id,
                code="LOAM",
                name_fr="Limon — analyse parcelle P01",
                name_en="Loam — measured",
                theta_fc_m3_m3=0.27,
                theta_wp_m3_m3=0.13,
                hydraulic_source="Analyse de laboratoire, échantillon P01, 2026-03",
                is_measured=True,
            )
        )

    async with databases.for_tenant(alpha.tenant_id).begin() as session:
        rows = (
            await session.execute(
                select(SoilProfile.theta_fc_m3_m3, SoilProfile.is_measured).where(
                    SoilProfile.code == "LOAM"
                )
            )
        ).all()
    # Les deux lignes sont visibles : la globale et la sienne. C'est au service
    # de préférer la mesure — la base ne cache pas la référence, elle l'expose
    # à côté, ce qui permet d'afficher l'écart.
    assert len(rows) == 2
    assert any(measured for _fc, measured in rows)

    async with databases.for_tenant(beta.tenant_id).begin() as session:
        beta_rows = (
            await session.execute(
                select(SoilProfile.is_measured).where(SoilProfile.code == "LOAM")
            )
        ).all()
    assert len(beta_rows) == 1
    assert beta_rows[0][0] is False


async def test_a_measured_override_is_marked_as_measured(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    """`is_measured` distingue une mesure locale d'une valeur publiée.

    Les deux sont des nombres dans la même colonne ; seul ce drapeau dit que
    l'une a été constatée sur la parcelle et l'autre lue dans une table.
    """
    alpha, _ = two_orgs
    async with databases.for_tenant(alpha.tenant_id).begin() as session:
        globals_measured = (
            await session.execute(
                select(SoilProfile.is_measured).where(SoilProfile.tenant_id.is_(None))
            )
        ).scalars()
        assert not any(globals_measured), (
            "a global reference row claims to be a measurement; published table "
            "values are not local measurements"
        )


async def test_the_database_refuses_a_crop_with_a_ky_and_no_source(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    """La contrainte de base double le test de données.

    Le test sur les fichiers attrape l'erreur au chargement ; celle-ci attrape
    toute écriture ultérieure, y compris une surcharge posée par une
    organisation des mois plus tard.
    """
    alpha, _ = two_orgs
    with pytest.raises(DBAPIError) as excinfo:
        async with databases.for_tenant(alpha.tenant_id).begin() as session:
            session.add(
                Crop(
                    id=uuid.uuid4(),
                    tenant_id=alpha.tenant_id,
                    code="INVENTED",
                    name_fr="Culture inventée",
                    name_en="Invented",
                    category=CropCategory.MARAICHAGE,
                    kc_initial=0.5,
                    kc_mid=1.0,
                    kc_end=0.7,
                    kc_source="aucune",
                    stage_lengths_source="aucune",
                    root_depth_min_m=0.3,
                    root_depth_max_m=0.6,
                    depletion_fraction_p=0.4,
                    root_and_depletion_source="aucune",
                    yield_response_factor_ky=1.1,
                    ky_source=None,  # ← interdit
                )
            )
    assert "ck_crops_ky_documented_or_explained" in str(excinfo.value)
