"""Accès, portabilité, effacement, traçabilité (loi 09-08 / CNDP).

Quatre droits, quatre vérifications. Ce ne sont pas des fonctions de confort :
un export incomplet présenté comme complet, ou une suppression qui laisse des
lignes derrière elle, sont exactement les manquements que la loi vise.

Le test le plus important du fichier est celui qui vérifie que l'export **suit
le modèle** : une liste de tables écrite à la main resterait vraie le jour où on
l'écrit et fausse au premier modèle ajouté.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import text

from app.db.base import tables_with_tenant_column
from app.db.session import Databases
from app.services.organisation_service import (
    EXPORT_EXCLUDED_TABLES,
    REDACTED_COLUMNS,
)
from tests.conftest import Org

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés sans marqueur.


async def _login(client: AsyncClient, org: Org) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": org.email, "password": org.password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _demote(databases: Databases, org: Org, role: str = "AGRONOME") -> None:
    async with databases.for_tenant(org.tenant_id).begin() as session:
        await session.execute(
            text("UPDATE app.users SET role = :r WHERE tenant_id = :t"),
            {"r": role, "t": org.tenant_id},
        )


# ---------------------------------------------------------------------------
# Accès : qui compose l'organisation
# ---------------------------------------------------------------------------
async def test_the_organisation_sheet_names_its_members_and_their_roles(
    client: AsyncClient, demo_org: Org
) -> None:
    headers = await _login(client, demo_org)
    body = (await client.get("/api/v1/organisation", headers=headers)).json()
    assert body["slug"]
    assert body["is_demo"] is True, "une organisation de démonstration le dit partout"
    assert body["members"], "une organisation sans membre n'existe pas"
    for member in body["members"]:
        assert member["role_label_fr"], "un rôle s'affiche en français"


async def test_the_last_administrator_cannot_be_demoted(
    client: AsyncClient, demo_org: Org
) -> None:
    """Une organisation sans administrateur ne peut plus qu'appeler au secours."""
    headers = await _login(client, demo_org)
    body = (await client.get("/api/v1/organisation", headers=headers)).json()
    admins = [m for m in body["members"] if m["role"] == "ADMIN"]
    assert len(admins) == 1

    response = await client.post(
        f"/api/v1/organisation/membres/{admins[0]['id']}/role",
        json={"role": "ANALYST"},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert "dernier administrateur" in response.text


# ---------------------------------------------------------------------------
# Portabilité
# ---------------------------------------------------------------------------
async def test_the_export_follows_the_model_rather_than_a_hand_written_list(
    client: AsyncClient, demo_org: Org
) -> None:
    """La table ajoutée le mois prochain doit être exportée le jour de sa création."""
    headers = await _login(client, demo_org)
    body = (await client.get("/api/v1/organisation/export", headers=headers)).json()

    expected = {
        name
        for name in tables_with_tenant_column()
        if name not in EXPORT_EXCLUDED_TABLES
    }
    assert set(body["tables"]) == expected
    assert body["row_counts"]["fields"] > 0, "le jeu de démonstration a des parcelles"


async def test_the_export_carries_no_authentication_secret(
    client: AsyncClient, demo_org: Org
) -> None:
    """Un condensat de mot de passe n'est la donnée personnelle de personne.

    C'est un moyen d'accès, et l'inclure dans un fichier que l'utilisateur
    transporte créerait un risque que la portabilité n'exige pas.
    """
    headers = await _login(client, demo_org)
    body = (await client.get("/api/v1/organisation/export", headers=headers)).json()
    for user in body["tables"]["users"]:
        assert not REDACTED_COLUMNS & set(user)
    assert "users.password_hash" in body["redacted_columns"]
    assert body["notice_fr"], "un fichier qui circule porte ses propres réserves"


async def test_the_export_contains_only_what_belongs_to_the_organisation(
    client: AsyncClient, databases: Databases, demo_org: Org, two_orgs: tuple[Org, Org]
) -> None:
    """Deux fuites possibles, opposées et toutes deux silencieuses.

    Vers l'extérieur : les données d'une autre organisation. Vers l'intérieur :
    le catalogue FAO global, que la politique RLS des tables de référentiel
    laisse lire — et qui serait présenté comme les données du client.
    """
    alpha, _ = two_orgs
    async with databases.for_tenant(alpha.tenant_id).begin() as session:
        await session.execute(
            text(
                "INSERT INTO app.sites (id, tenant_id, code, name_fr, site_type, "
                "latitude, longitude, data_state, data_origin, source_id) VALUES "
                "(gen_random_uuid(), :t, 'SITE_ALPHA', 'Ferme alpha', 'FARM', 30.4, "
                "-9.6, 'OBSERVED', 'MANUAL_ENTRY', 'manual-entry')"
            ),
            {"t": alpha.tenant_id},
        )

    headers = await _login(client, demo_org)
    body = (await client.get("/api/v1/organisation/export", headers=headers)).json()

    codes = {row["code"] for row in body["tables"]["sites"]}
    assert "SITE_ALPHA" not in codes, "aucune donnée d'une autre organisation"
    for row in body["tables"]["crops"]:
        assert row["tenant_id"] == str(demo_org.tenant_id), (
            "une ligne de référentiel globale n'est pas la donnée du client"
        )


async def test_exporting_is_reserved_to_an_administrator(
    client: AsyncClient, databases: Databases, demo_org: Org
) -> None:
    await _demote(databases, demo_org)
    headers = await _login(client, demo_org)
    response = await client.get("/api/v1/organisation/export", headers=headers)
    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# Traçabilité
# ---------------------------------------------------------------------------
async def test_reading_the_journal_is_itself_written_to_the_journal(
    client: AsyncClient, demo_org: Org
) -> None:
    """Un registre qu'on peut lire sans laisser de trace ne prouve plus rien."""
    headers = await _login(client, demo_org)
    first = await client.get("/api/v1/organisation/journal", headers=headers)
    assert first.status_code == 200, first.text

    second = (await client.get("/api/v1/organisation/journal", headers=headers)).json()
    reads = [entry for entry in second if entry["action"] == "audit:read"]
    assert reads, "la première consultation doit figurer dans la seconde"
    assert reads[0]["actor_email"] == demo_org.email


async def test_an_export_leaves_a_trace_naming_its_author(
    client: AsyncClient, demo_org: Org
) -> None:
    headers = await _login(client, demo_org)
    await client.get("/api/v1/organisation/export", headers=headers)
    journal = (await client.get("/api/v1/organisation/journal", headers=headers)).json()
    exports = [e for e in journal if e["action"] == "organisation:export"]
    assert exports and exports[0]["actor_email"] == demo_org.email


async def test_the_journal_is_reserved_to_an_administrator(
    client: AsyncClient, databases: Databases, demo_org: Org
) -> None:
    """Le journal dit ce que les collègues ont consulté. Ce n'est pas public."""
    await _demote(databases, demo_org)
    headers = await _login(client, demo_org)
    response = await client.get("/api/v1/organisation/journal", headers=headers)
    assert response.status_code == 403, response.text


async def test_one_organisation_never_sees_another_s_journal(
    client: AsyncClient, databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    alpha, beta = two_orgs
    alpha_headers = await _login(client, alpha)
    await client.get("/api/v1/organisation/export", headers=alpha_headers)

    beta_headers = await _login(client, beta)
    journal = (
        await client.get("/api/v1/organisation/journal", headers=beta_headers)
    ).json()
    assert all(entry["actor_email"] != alpha.email for entry in journal)


# ---------------------------------------------------------------------------
# Effacement
# ---------------------------------------------------------------------------
async def test_deleting_a_member_pseudonymises_their_trace_instead_of_erasing_it(
    client: AsyncClient, databases: Databases, demo_org: Org
) -> None:
    """Le journal est la preuve des accès subis par les autres.

    L'effacer au nom du droit de l'un retirerait aux autres le leur. Le
    courriel disparaît ; la trace de l'acte demeure.
    """
    headers = await _login(client, demo_org)
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        victim = uuid.uuid4()
        await session.execute(
            text(
                "INSERT INTO app.users (id, tenant_id, email, full_name, "
                "password_hash, role, is_active) VALUES (:id, :t, :e, 'À supprimer', "
                "'x', 'ANALYST', true)"
            ),
            {"id": victim, "t": demo_org.tenant_id, "e": f"{victim.hex[:8]}@example.ma"},
        )
        await session.execute(
            text(
                "INSERT INTO app.audit_log (id, tenant_id, actor_user_id, "
                "actor_email, action, resource_type, outcome) VALUES "
                "(gen_random_uuid(), :t, :u, :e, 'field:read', 'FIELD', 'SUCCESS')"
            ),
            {
                "t": demo_org.tenant_id,
                "u": victim,
                "e": f"{victim.hex[:8]}@example.ma",
            },
        )

    response = await client.delete(
        f"/api/v1/organisation/membres/{victim}", headers=headers
    )
    assert response.status_code == 200, response.text

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        remaining = (
            await session.execute(
                text("SELECT count(*) FROM app.users WHERE id = :id"), {"id": victim}
            )
        ).scalar_one()
        trace = (
            await session.execute(
                text(
                    "SELECT actor_email FROM app.audit_log WHERE actor_user_id = :id"
                ),
                {"id": victim},
            )
        ).scalar_one()
    assert remaining == 0, "le compte est supprimé"
    assert trace.startswith("supprimé-"), "la trace demeure, pseudonymisée"
    assert "@" not in trace, "plus aucun courriel"


async def test_an_administrator_cannot_delete_their_own_account(
    client: AsyncClient, demo_org: Org
) -> None:
    """Sinon l'organisation se retrouve sans administrateur, en un clic."""
    headers = await _login(client, demo_org)
    body = (await client.get("/api/v1/organisation", headers=headers)).json()
    me = next(m for m in body["members"] if m["email"] == demo_org.email)
    response = await client.delete(
        f"/api/v1/organisation/membres/{me['id']}", headers=headers
    )
    assert response.status_code == 422, response.text


async def test_deleting_the_organisation_demands_its_name_typed_out(
    client: AsyncClient, demo_org: Org
) -> None:
    """Une case à cocher se coche par réflexe ; un nom se recopie en le lisant."""
    headers = await _login(client, demo_org)
    response = await client.request(
        "DELETE",
        "/api/v1/organisation",
        json={"confirmation": "oui"},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert "Recopiez exactement" in response.text


async def test_deleting_the_organisation_removes_every_row_it_owned(
    client: AsyncClient, databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    """Et ne touche ni au référentiel global, ni à l'autre organisation."""
    alpha, beta = two_orgs
    async with databases.for_tenant(beta.tenant_id).begin() as session:
        await session.execute(
            text(
                "INSERT INTO app.sites (id, tenant_id, code, name_fr, site_type, "
                "latitude, longitude, data_state, data_origin, source_id) VALUES "
                "(gen_random_uuid(), :t, 'B01', 'Ferme beta', 'FARM', 30.4, -9.6, "
                "'OBSERVED', 'MANUAL_ENTRY', 'manual-entry')"
            ),
            {"t": beta.tenant_id},
        )

    async with databases.for_tenant(alpha.tenant_id).begin() as session:
        slug = (
            await session.execute(
                text("SELECT slug FROM app.tenants WHERE id = :id"),
                {"id": alpha.tenant_id},
            )
        ).scalar_one()

    headers = await _login(client, alpha)
    response = await client.request(
        "DELETE",
        "/api/v1/organisation",
        json={"confirmation": slug},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["total_rows"] >= 1
    assert "définitivement" in receipt["message_fr"]

    # L'autre organisation est intacte, et le catalogue global aussi.
    async with databases.for_tenant(beta.tenant_id).begin() as session:
        beta_sites = (
            await session.execute(text("SELECT count(*) FROM app.sites"))
        ).scalar_one()
        global_crops = (
            await session.execute(
                text("SELECT count(*) FROM app.crops WHERE tenant_id IS NULL")
            )
        ).scalar_one()
    assert beta_sites == 1
    assert global_crops > 0, "le catalogue FAO n'appartient à personne en particulier"


async def test_deleting_the_organisation_is_reserved_to_an_administrator(
    client: AsyncClient, databases: Databases, demo_org: Org
) -> None:
    await _demote(databases, demo_org)
    headers = await _login(client, demo_org)
    response = await client.request(
        "DELETE", "/api/v1/organisation", json={"confirmation": "x"}, headers=headers
    )
    assert response.status_code == 403, response.text


# ---------------------------------------------------------------------------
# Ce qui est déclaré, et ce qui ne l'est pas
# ---------------------------------------------------------------------------
async def test_an_undeclared_residency_is_said_undeclared_and_not_guessed(
    client: AsyncClient, demo_org: Org
) -> None:
    """« Maroc » par défaut serait une affirmation que personne n'a vérifiée."""
    headers = await _login(client, demo_org)
    body = (await client.get("/api/v1/organisation/conformite", headers=headers)).json()
    assert body["residency_country"] is None
    assert any("n'est pas déclarée" in s for s in body["statements_fr"])


async def test_an_announced_retention_that_is_not_applied_says_so(
    client: AsyncClient, demo_org: Org
) -> None:
    """Annoncer une durée qu'aucune purge n'applique est une conformité de façade."""
    headers = await _login(client, demo_org)
    body = (await client.get("/api/v1/organisation/conformite", headers=headers)).json()
    assert body["audit_retention_days"] > 0
    assert body["retention_enforced"] is False
    assert any("aucune purge" in limit for limit in body["limitations_fr"])
