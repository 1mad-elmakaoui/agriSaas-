"""Isolation multi-organisations, prouvée à cinq couches.

C'est le test qui décide si la phase 1 est terminée. Les cinq couches sont
indépendantes : chacune tient quand les autres sont le bug, et c'est la raison
pour laquelle elles coexistent au lieu de se remplacer.

    1. jeton      — le tenant vient d'un jeton signé, jamais d'une entrée
    2. API        — une ressource d'une autre organisation est un 404
    3. dépôt      — aucune méthode ne permet d'omettre le filtre
    4. RLS `app`  — la base filtre, même quand le dépôt est contourné
    5. RLS vues   — la surface analytique filtre aussi, et ne voit pas les
                    tables de base

Chaque test prouve **les deux sens**. « L'organisation A ne voit pas la ligne de
B » réussit aussi quand la ligne de B n'existe pas ; sans preuve préalable de
visibilité, la suite passerait sur une base vide en démontrant l'absence de
données plutôt que la présence d'une frontière.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.security import create_access_token
from app.db.base import Site
from app.db.session import Databases
from app.domain.enums import DataOrigin, DataState, SiteType
from app.domain.provenance import SOURCE_MANUAL_ENTRY
from app.repositories.tenant import TenantRepository
from tests.conftest import Org

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés, et un marqueur
# explicite ferait avertir pytest sur les tests synchrones du même module.


async def _make_site(databases: Databases, org: Org, code: str) -> uuid.UUID:
    site_id = uuid.uuid4()
    async with databases.for_tenant(org.tenant_id).begin() as session:
        session.add(
            Site(
                id=site_id,
                tenant_id=org.tenant_id,
                code=code,
                name_fr=f"Site {code}",
                site_type=SiteType.FARM,
                latitude=30.4,
                longitude=-9.6,
                data_state=DataState.OBSERVED.value,
                data_origin=DataOrigin.MANUAL_ENTRY.value,
                source_id=SOURCE_MANUAL_ENTRY.id,
            )
        )
    return site_id


# -- couche 4 : RLS sur le schéma applicatif ----------------------------------


async def test_rls_filters_in_both_directions(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    alpha, beta = two_orgs
    await _make_site(databases, beta, "BETA-1")

    # Sens 1 : la ligne existe et son organisation la voit. Sans ce temps, le
    # second contrôle passerait aussi sur une base vide.
    async with databases.for_tenant(beta.tenant_id).begin() as session:
        visible = (
            await session.execute(
                text("SELECT count(*) FROM app.sites WHERE code = 'BETA-1'")
            )
        ).scalar_one()
    assert visible == 1, "la fixture n'est pas visible pour sa propre organisation"

    # Sens 2 : l'autre organisation ne la voit pas.
    async with databases.for_tenant(alpha.tenant_id).begin() as session:
        leaked = (
            await session.execute(
                text("SELECT count(*) FROM app.sites WHERE code = 'BETA-1'")
            )
        ).scalar_one()
    assert leaked == 0


async def test_unbound_session_fails_closed_rather_than_returning_everything(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    """Sans organisation posée, la lecture doit **échouer**, pas rendre un jeu vide.

    C'est délibéré et c'est le contraire de l'intuition. `current_setting` est
    appelé sans `missing_ok` : un GUC absent lève une erreur. Avec `missing_ok`,
    la politique serait fausse partout et l'appelant recevrait zéro ligne — que
    l'agent d'analyse raconterait comme « vous n'avez aucune expédition ce
    mois-ci ». Une panne muette est ici pire qu'une panne bruyante.
    """
    alpha, _ = two_orgs
    await _make_site(databases, alpha, "ALPHA-1")

    with pytest.raises(Exception) as excinfo:
        async with databases.unscoped_session() as session:
            await session.execute(text("SELECT count(*) FROM app.sites"))
    # Deux formes d'échec selon l'état de la connexion, et les deux conviennent :
    # sur une connexion neuve le paramètre est inconnu ; sur une connexion déjà
    # utilisée, `SET LOCAL` en a fait un paramètre connu que la fin de
    # transaction a remis à la chaîne vide, laquelle ne se convertit pas en
    # uuid. Ce qui compte n'est pas le message, mais qu'aucune ligne ne sorte.
    message = str(excinfo.value)
    assert (
        "app.current_tenant" in message
        or "invalid input syntax for type uuid" in message
    ), message


async def test_write_cannot_cross_the_boundary(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    """`WITH CHECK` empêche d'écrire *dans* une autre organisation.

    Une politique qui ne filtrerait qu'en lecture laisserait injecter des lignes
    chez un autre client — plus grave qu'une fuite, parce qu'invisible pour lui.
    """
    alpha, beta = two_orgs
    with pytest.raises(Exception) as excinfo:
        async with databases.for_tenant(alpha.tenant_id).begin() as session:
            await session.execute(
                text(
                    "INSERT INTO app.sites "
                    "(id, tenant_id, code, name_fr, site_type, latitude, longitude, "
                    " data_state, data_origin, source_id) "
                    "VALUES (gen_random_uuid(), :t, 'FRAUDE', 'x', 'FARM', 0, 0, "
                    "'OBSERVED', 'MANUAL_ENTRY', 'manual-entry')"
                ),
                {"t": beta.tenant_id},
            )
    assert "row-level security" in str(excinfo.value).lower()


# -- couche 3 : dépôt ---------------------------------------------------------


async def test_repository_scopes_every_read(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    alpha, beta = two_orgs
    await _make_site(databases, alpha, "ALPHA-R")
    await _make_site(databases, beta, "BETA-R")

    async with databases.for_tenant(alpha.tenant_id).begin() as session:
        codes = {
            s.code for s in await TenantRepository(session, alpha.context).list(Site)
        }
    assert codes == {"ALPHA-R"}


async def test_repository_overwrites_a_supplied_tenant_id(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    """Une organisation fournie par l'appelant est écrasée, pas validée.

    Valider laisserait un appelant découvrir, par le message d'erreur, qu'un
    identifiant d'organisation existe.
    """
    alpha, beta = two_orgs
    async with databases.for_tenant(alpha.tenant_id).begin() as session:
        site = Site(
            id=uuid.uuid4(),
            tenant_id=beta.tenant_id,  # tentative
            code="ECRASE",
            name_fr="x",
            site_type=SiteType.FARM,
            latitude=0,
            longitude=0,
            data_state=DataState.OBSERVED.value,
            data_origin=DataOrigin.MANUAL_ENTRY.value,
            source_id=SOURCE_MANUAL_ENTRY.id,
        )
        stored = await TenantRepository(session, alpha.context).add(site)
        assert stored.tenant_id == alpha.tenant_id


async def test_repository_refuses_a_table_that_is_not_tenant_scoped(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    alpha, _ = two_orgs
    from app.db.base import Tenant

    async with databases.for_tenant(alpha.tenant_id).begin() as session:
        with pytest.raises(TypeError, match="multi-tenant"):
            await TenantRepository(session, alpha.context).list(Tenant)  # type: ignore[type-var]


# -- couche 5 : surface analytique -------------------------------------------


async def test_analytics_view_filters_in_both_directions(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    alpha, beta = two_orgs
    await _make_site(databases, beta, "BETA-AN")

    async with databases.analytics_for_tenant(beta.tenant_id).connection() as conn:
        visible = (
            await conn.execute(
                text("SELECT count(*) FROM analytics.v_sites WHERE code = 'BETA-AN'")
            )
        ).scalar_one()
    assert visible == 1, "la vue ne montre pas la ligne à sa propre organisation"

    async with databases.analytics_for_tenant(alpha.tenant_id).connection() as conn:
        leaked = (
            await conn.execute(
                text("SELECT count(*) FROM analytics.v_sites WHERE code = 'BETA-AN'")
            )
        ).scalar_one()
    assert leaked == 0


async def test_analytics_role_cannot_reach_base_tables(databases: Databases) -> None:
    """Le confinement ne repose pas sur le validateur AST.

    Si le rôle analytique atteignait `app.sites`, une requête générée qui
    échapperait au validateur lirait les tables de base. Il ne l'atteint pas :
    l'échec est un refus de privilège, en amont de tout code applicatif.
    """
    scope = databases.analytics_for_tenant(uuid.uuid4())
    with pytest.raises(Exception) as excinfo:
        async with scope.connection() as conn:
            await conn.execute(text("SELECT count(*) FROM app.sites"))
    assert "permission denied" in str(excinfo.value).lower()


async def test_analytics_role_cannot_write(databases: Databases) -> None:
    scope = databases.analytics_for_tenant(uuid.uuid4())
    with pytest.raises(DBAPIError):
        async with scope.connection() as conn:
            await conn.execute(text("CREATE TEMP TABLE _probe (i int)"))


# -- couches 1 et 2 : jeton et API -------------------------------------------


async def test_api_refuses_another_organisations_resource(
    client, databases: Databases, two_orgs: tuple[Org, Org], settings
) -> None:
    """Un identifiant valide d'une autre organisation est un 404, jamais un 403.

    Confirmer l'existence de la ressource serait déjà une divulgation.
    """
    alpha, beta = two_orgs
    beta_site = await _make_site(databases, beta, "BETA-API")

    token = create_access_token(
        alpha.context, secret=settings.jwt_secret, ttl_minutes=60
    )
    headers = {"Authorization": f"Bearer {token}"}

    # Sens 1 : beta voit sa propre ressource.
    beta_token = create_access_token(
        beta.context, secret=settings.jwt_secret, ttl_minutes=60
    )
    own = await client.get(
        f"/api/v1/sites/{beta_site}", headers={"Authorization": f"Bearer {beta_token}"}
    )
    assert own.status_code == 200, own.text

    # Sens 2 : alpha ne la voit pas, et l'apprend par un 404.
    response = await client.get(f"/api/v1/sites/{beta_site}", headers=headers)
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_api_requires_a_token(client) -> None:
    response = await client.get("/api/v1/sites")
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "unauthenticated"
    assert body["message_fr"] == "Authentification requise."


async def test_a_forged_token_is_refused(client, two_orgs: tuple[Org, Org]) -> None:
    """Le tenant vient d'une signature, pas d'une affirmation.

    Un jeton bien formé mais signé avec une autre clé porte exactement les
    mêmes revendications : seule la signature les distingue.
    """
    alpha, _ = two_orgs
    forged = create_access_token(alpha.context, secret="wrong-secret", ttl_minutes=60)
    response = await client.get(
        "/api/v1/sites", headers={"Authorization": f"Bearer {forged}"}
    )
    assert response.status_code == 401


async def test_the_api_offers_no_way_to_name_another_tenant(client, settings, two_orgs) -> None:
    """Aucun champ d'entrée ne permet de désigner une organisation.

    C'est la forme concrète de « le tenant n'est jamais un paramètre » : ce
    n'est pas qu'une valeur fournie soit rejetée, c'est qu'il n'existe aucun
    champ pour l'exprimer.
    """
    alpha, beta = two_orgs
    token = create_access_token(alpha.context, secret=settings.jwt_secret, ttl_minutes=60)
    response = await client.post(
        "/api/v1/sites",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "code": "TENTATIVE",
            "name_fr": "Site",
            "site_type": "FARM",
            "latitude": 30.0,
            "longitude": -9.0,
            "tenant_id": str(beta.tenant_id),
        },
    )
    assert response.status_code == 422
