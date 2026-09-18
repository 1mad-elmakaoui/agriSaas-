"""Inscription autonome, et l'ajout d'un membre par un administrateur.

La §6 demande un parcours sans intermédiaire. Ce que ces tests fixent est ce que
l'inscription **ne** fait pas : elle ne fabrique aucune donnée, elle ne
provisionne pas un plan que personne n'a vendu, et elle n'ouvre pas une porte
qu'un script pourrait franchir mille fois.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import text

from app.core.rate_limit import SIGNUP_LIMIT
from app.db.session import Databases
from app.services.signup_service import slugify
from tests.conftest import Org

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés sans marqueur.


def _payload(suffix: str) -> dict[str, str]:
    return {
        "organisation_name": f"Coopérative Aït Melloul {suffix}",
        "full_name": "Rachid Amrani",
        "email": f"rachid-{suffix}@example.ma",
        "password": "un-mot-de-passe-assez-long",
    }


async def _cleanup(databases: Databases, email: str) -> None:
    import os

    from sqlalchemy.ext.asyncio import create_async_engine

    owner = create_async_engine(os.environ["ATLAS_TEST_OWNER_DATABASE_URL"])
    async with owner.begin() as conn:
        tenant = (
            await conn.execute(
                text("SELECT tenant_id FROM app.user_directory WHERE email = :e"),
                {"e": email},
            )
        ).scalar_one_or_none()
        if tenant is not None:
            await conn.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": str(tenant)},
            )
            await conn.execute(
                text("DELETE FROM app.audit_log WHERE tenant_id = :t"), {"t": tenant}
            )
            await conn.execute(
                text("DELETE FROM app.user_directory WHERE tenant_id = :t"),
                {"t": tenant},
            )
            await conn.execute(
                text("DELETE FROM app.users WHERE tenant_id = :t"), {"t": tenant}
            )
            await conn.execute(
                text("DELETE FROM app.tenants WHERE id = :t"), {"t": tenant}
            )
    await owner.dispose()


# ---------------------------------------------------------------------------
# L'identifiant lisible
# ---------------------------------------------------------------------------
def test_the_slug_transliterates_instead_of_dropping_accents() -> None:
    """Un identifiant que l'administrateur devra recopier doit rester lisible.

    Sans translittération, « Coopérative Aït Melloul » deviendrait
    « coop-rative-a-t-melloul » — méconnaissable au moment précis où il faut le
    reconnaître, c'est-à-dire pour confirmer une suppression.
    """
    assert slugify("Coopérative Aït Melloul") == "cooperative-ait-melloul"
    assert slugify("  Ferme  du  Souss  ") == "ferme-du-souss"
    assert slugify("!!!") == "organisation"


# ---------------------------------------------------------------------------
# L'inscription
# ---------------------------------------------------------------------------
async def test_signing_up_creates_an_empty_organisation_and_opens_a_session(
    client: AsyncClient, databases: Databases
) -> None:
    """Ni site, ni parcelle, ni donnée fabriquée. Et la session est déjà ouverte."""
    payload = _payload(uuid.uuid4().hex[:6])
    try:
        response = await client.post("/api/v1/auth/inscription", json=payload)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["role"] == "ADMIN", "le premier compte administre son organisation"
        assert body["is_demo"] is False, "une organisation réelle n'est pas une démonstration"
        assert body["access_token"]

        headers = {"Authorization": f"Bearer {body['access_token']}"}
        fields = await client.get("/api/v1/parcelles", headers=headers)
        assert fields.json() == [], "rien n'est fabriqué à l'inscription"

        state = (await client.get("/api/v1/demarrage", headers=headers)).json()
        assert state["complete"] is False
        assert all(step["done"] is False for step in state["steps"])
    finally:
        await _cleanup(databases, payload["email"])


async def test_a_new_organisation_reads_the_global_catalogue_without_a_copy(
    client: AsyncClient, databases: Databases
) -> None:
    """Le référentiel FAO est global : rien n'est recopié pour une nouvelle venue."""
    payload = _payload(uuid.uuid4().hex[:6])
    try:
        body = (await client.post("/api/v1/auth/inscription", json=payload)).json()
        headers = {"Authorization": f"Bearer {body['access_token']}"}
        choices = (await client.get("/api/v1/demarrage/choix", headers=headers)).json()
        assert choices["crops"], "les cultures FAO sont lisibles immédiatement"
        assert all(choice["is_local"] is False for choice in choices["crops"])
        assert choices["sites"] == [], "mais aucun site : il n'y a rien à elle"
    finally:
        await _cleanup(databases, payload["email"])


async def test_a_new_organisation_starts_on_the_smallest_plan(
    client: AsyncClient, databases: Databases
) -> None:
    """Provisionner mieux reviendrait à vendre sans qu'un administrateur l'ait décidé."""
    payload = _payload(uuid.uuid4().hex[:6])
    try:
        body = (await client.post("/api/v1/auth/inscription", json=payload)).json()
        headers = {"Authorization": f"Bearer {body['access_token']}"}
        subscription = (await client.get("/api/v1/abonnement", headers=headers)).json()
        assert subscription["plan"]["code"] == "COOPERATIVE"
    finally:
        await _cleanup(databases, payload["email"])


async def test_two_organisations_of_the_same_name_get_distinct_identifiers(
    client: AsyncClient, databases: Databases
) -> None:
    """Deux coopératives peuvent légitimement porter le même nom."""
    first = _payload(uuid.uuid4().hex[:6])
    second = _payload(uuid.uuid4().hex[:6])
    second["organisation_name"] = first["organisation_name"]
    try:
        one = (await client.post("/api/v1/auth/inscription", json=first)).json()
        two = (await client.post("/api/v1/auth/inscription", json=second)).json()
        slugs = []
        for body in (one, two):
            headers = {"Authorization": f"Bearer {body['access_token']}"}
            slugs.append(
                (await client.get("/api/v1/organisation", headers=headers)).json()["slug"]
            )
        assert slugs[0] != slugs[1]
        assert slugs[0].startswith("cooperative-ait-melloul")
        assert slugs[1].startswith("cooperative-ait-melloul")
    finally:
        await _cleanup(databases, first["email"])
        await _cleanup(databases, second["email"])


async def test_an_email_already_in_use_is_refused_by_name(
    client: AsyncClient, demo_org: Org
) -> None:
    """Assumé : un formulaire d'inscription qui échoue en silence est inutilisable.

    La route de **connexion**, elle, continue de ne jamais distinguer ses causes
    d'échec — c'est là que l'énumération de comptes aurait un intérêt.
    """
    response = await client.post(
        "/api/v1/auth/inscription",
        json={
            "organisation_name": "Une autre coopérative",
            "full_name": "Quelqu'un",
            "email": demo_org.email,
            "password": "un-mot-de-passe-assez-long",
        },
    )
    assert response.status_code == 422, response.text
    assert "déjà rattaché" in response.text


async def test_a_short_password_is_refused(client: AsyncClient) -> None:
    payload = _payload(uuid.uuid4().hex[:6]) | {"password": "court"}
    response = await client.post("/api/v1/auth/inscription", json=payload)
    assert response.status_code == 422, response.text


async def test_signing_up_is_rate_limited_far_below_the_general_threshold(
    client: AsyncClient, app: object
) -> None:
    """Un appel réussi crée une organisation. Un script en créerait des milliers."""
    limiter = getattr(app, "state").rate_limiter  # noqa: B009 - app est typé large ici
    limiter.reset()
    codes = []
    for _ in range(SIGNUP_LIMIT.max_calls + 1):
        # Refusées pour mot de passe trop court : le seuil compte les tentatives,
        # pas les succès — sinon un script sonderait librement les courriels.
        response = await client.post(
            "/api/v1/auth/inscription",
            json=_payload(uuid.uuid4().hex[:6]) | {"password": "court"},
        )
        codes.append(response.status_code)
    assert codes[-1] == 429
    assert SIGNUP_LIMIT.max_calls < 120, "beaucoup plus strict que le seuil général"


# ---------------------------------------------------------------------------
# L'ajout d'un membre
# ---------------------------------------------------------------------------
async def test_an_administrator_adds_a_member_with_a_role(
    client: AsyncClient, demo_org: Org
) -> None:
    headers = {
        "Authorization": "Bearer "
        + (
            await client.post(
                "/api/v1/auth/login",
                json={"email": demo_org.email, "password": demo_org.password},
            )
        ).json()["access_token"]
    }
    email = f"agronome-{uuid.uuid4().hex[:6]}@example.ma"
    response = await client.post(
        "/api/v1/organisation/membres",
        json={
            "email": email,
            "full_name": "Nouvelle agronome",
            "role": "AGRONOME",
            "password": "un-mot-de-passe-assez-long",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    members = {m["email"]: m for m in response.json()["members"]}
    assert members[email]["role"] == "AGRONOME"
    assert members[email]["role_label_fr"] == "Agronome"

    # Le compte créé se connecte réellement : une fiche sans aiguillage serait
    # un compte qui ne peut pas entrer.
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "un-mot-de-passe-assez-long"},
    )
    assert login.status_code == 200, login.text


async def test_adding_a_member_is_reserved_to_an_administrator(
    client: AsyncClient, databases: Databases, demo_org: Org
) -> None:
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        await session.execute(
            text("UPDATE app.users SET role = 'AGRONOME' WHERE tenant_id = :t"),
            {"t": demo_org.tenant_id},
        )
    token = (
        await client.post(
            "/api/v1/auth/login",
            json={"email": demo_org.email, "password": demo_org.password},
        )
    ).json()["access_token"]
    response = await client.post(
        "/api/v1/organisation/membres",
        json={
            "email": f"x-{uuid.uuid4().hex[:6]}@example.ma",
            "full_name": "Quelqu'un",
            "role": "ANALYST",
            "password": "un-mot-de-passe-assez-long",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403, response.text
