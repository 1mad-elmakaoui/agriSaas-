"""Le premier parcours, de bout en bout.

La promesse tenue ici est une durée : moins de dix minutes entre un compte vide
et un premier avis d'irrigation lisible. Ce qui fait rater cette promesse n'est
pas la lenteur de la saisie mais l'obligation de deviner — quel code de culture
existe, pourquoi l'avis est bloqué, ce qu'il reste à faire.

Les tests exercent donc le parcours **par le vrai chemin HTTP**, et vérifient à
chaque étape les deux choses qu'un raccourci casserait en silence : la
provenance écrite par le serveur, et l'absence de valeur inventée.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import text

from app.db.session import Databases
from tests.conftest import Org

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés sans marqueur.


async def _login(client: AsyncClient, org: Org) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": org.email, "password": org.password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _payload(code: str, choices: dict[str, list[dict[str, str]]]) -> dict[str, object]:
    return {
        "code": code,
        "name_fr": "Parcelle du bas",
        "site_code": choices["sites"][0]["code"],
        "area_ha": 2.5,
        "latitude": 30.42,
        "longitude": -9.58,
        "crop_code": choices["crops"][0]["code"],
        "soil_code": choices["soils"][0]["code"],
        "irrigation_system_code": choices["irrigation_systems"][0]["code"],
    }


# ---------------------------------------------------------------------------
# Le parcours
# ---------------------------------------------------------------------------
async def test_the_first_value_path_runs_end_to_end(
    client: AsyncClient, demo_org: Org
) -> None:
    """Choix du catalogue, parcelle, relevé, avis. Sans deviner une seule fois."""
    headers = await _login(client, demo_org)

    choices = (await client.get("/api/v1/demarrage/choix", headers=headers)).json()
    assert choices["crops"] and choices["soils"] and choices["irrigation_systems"]
    assert choices["sites"], "sans site d'exploitation, aucune parcelle ne peut naître"

    created = await client.post(
        "/api/v1/parcelles", json=_payload("N01", choices), headers=headers
    )
    assert created.status_code == 201, created.text
    assert created.json()["code"] == "N01"

    reading = await client.post(
        "/api/v1/parcelles/N01/humidite", json={"value_pct": 21.5}, headers=headers
    )
    assert reading.status_code == 201, reading.text

    advice = await client.get("/api/v1/parcelles/N01/irrigation", headers=headers)
    assert advice.status_code == 200, advice.text
    body = advice.json()
    assert body["field_code"] == "N01"
    assert body["headline_fr"], "un avis sans phrase n'est pas un avis"
    assert body["recommendation_label_fr"]


async def test_the_server_says_what_remains_to_be_done(
    client: AsyncClient, demo_org: Org
) -> None:
    """Les étapes viennent du serveur, pas d'une liste écrite en dur dans l'écran.

    Le jour où une étape disparaît, l'interface cesse de la demander sans qu'on
    la modifie.
    """
    headers = await _login(client, demo_org)
    state = (await client.get("/api/v1/demarrage", headers=headers)).json()
    assert [step["key"] for step in state["steps"]] == [
        "site",
        "field",
        "moisture",
        "recommendation",
    ]
    for step in state["steps"]:
        assert step["title_fr"] and step["detail_fr"]
        if step["done"]:
            assert step["action_fr"] is None, "une étape franchie n'a pas d'action"
        else:
            assert step["action_fr"], "une étape à faire doit dire quoi faire"


async def test_an_empty_organisation_starts_at_the_first_step(
    client: AsyncClient, two_orgs: tuple[Org, Org]
) -> None:
    """Un compte neuf n'a ni site, ni parcelle, ni relevé — et on le lui dit."""
    alpha, _ = two_orgs
    headers = await _login(client, alpha)
    state = (await client.get("/api/v1/demarrage", headers=headers)).json()
    assert state["complete"] is False
    assert state["first_field_code"] is None
    assert all(step["done"] is False for step in state["steps"])


# ---------------------------------------------------------------------------
# La provenance, décidée par le serveur
# ---------------------------------------------------------------------------
async def test_a_hand_typed_reading_is_marked_as_such(
    client: AsyncClient, demo_org: Org
) -> None:
    """Une saisie au clavier est `OBSERVED` et « saisie manuelle », jamais autre chose."""
    headers = await _login(client, demo_org)
    response = await client.post(
        "/api/v1/parcelles/P03/humidite", json={"value_pct": 18.0}, headers=headers
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["state"] == "OBSERVED"
    assert body["origin"] == "MANUAL_ENTRY"
    assert body["origin_label_fr"]
    assert body["depth_cm"] is None, "aucune profondeur inventée"


async def test_a_client_cannot_claim_its_typing_came_from_a_sensor(
    client: AsyncClient, demo_org: Org
) -> None:
    """`extra="forbid"` : la tentative reçoit une 422, pas un silence."""
    headers = await _login(client, demo_org)
    response = await client.post(
        "/api/v1/parcelles/P03/humidite",
        json={"value_pct": 18.0, "data_origin": "SENSOR", "sensor_id": "S-1"},
        headers=headers,
    )
    assert response.status_code == 422, response.text


async def test_a_reading_cannot_be_dated_in_the_future(
    client: AsyncClient, demo_org: Org
) -> None:
    """Une mesure future est soit une faute de frappe, soit une invention."""
    headers = await _login(client, demo_org)
    later = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    response = await client.post(
        "/api/v1/parcelles/P03/humidite",
        json={"value_pct": 18.0, "recorded_at": later},
        headers=headers,
    )
    assert response.status_code == 422, response.text
    assert "futur" in response.text


# ---------------------------------------------------------------------------
# Rien d'inventé
# ---------------------------------------------------------------------------
async def test_a_field_without_a_flow_rate_gets_no_duration(
    client: AsyncClient, demo_org: Org
) -> None:
    """Pas de débit, pas de durée. Un débit par défaut produirait un arrosage faux.

    Et le blocage doit se **dire** : une durée absente sans explication se lit
    comme une panne.
    """
    headers = await _login(client, demo_org)
    choices = (await client.get("/api/v1/demarrage/choix", headers=headers)).json()
    await client.post(
        "/api/v1/parcelles", json=_payload("N02", choices), headers=headers
    )

    # Sans relevé, le moteur refuse et dit quoi faire : c'est l'étape 3 du
    # parcours, pas une panne.
    blocked = await client.get("/api/v1/parcelles/N02/irrigation", headers=headers)
    assert blocked.status_code == 422, blocked.text
    assert "humidité" in blocked.json()["remedy_fr"]

    await client.post(
        "/api/v1/parcelles/N02/humidite", json={"value_pct": 19.0}, headers=headers
    )
    response = await client.get("/api/v1/parcelles/N02/irrigation", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["net_requirement_mm"] is not None, "le besoin net, lui, se calcule"
    assert body["duration_minutes"] is None
    assert body["duration_label_fr"] is None
    assert body["estimated_cost_mad"] is None, "pas de tarif, pas de coût"


async def test_an_unknown_crop_names_the_catalogue(
    client: AsyncClient, demo_org: Org
) -> None:
    """« Culture inconnue » oblige à deviner. La liste se corrige en une fois."""
    headers = await _login(client, demo_org)
    choices = (await client.get("/api/v1/demarrage/choix", headers=headers)).json()
    payload = _payload("N03", choices) | {"crop_code": "PASTEQUE_IMAGINAIRE"}
    response = await client.post("/api/v1/parcelles", json=payload, headers=headers)
    assert response.status_code == 422, response.text
    assert choices["crops"][0]["code"] in response.text


async def test_a_duplicate_field_code_is_refused_by_name(
    client: AsyncClient, demo_org: Org
) -> None:
    """Deux P03 rendraient toute recommandation ambiguë."""
    headers = await _login(client, demo_org)
    choices = (await client.get("/api/v1/demarrage/choix", headers=headers)).json()
    response = await client.post(
        "/api/v1/parcelles", json=_payload("P03", choices), headers=headers
    )
    assert response.status_code == 422, response.text
    assert "P03" in response.text


async def test_the_stock_quota_refuses_before_writing(
    client: AsyncClient, databases: Databases, demo_org: Org
) -> None:
    """Un plafond vérifié après l'écriture aurait laissé une parcelle à supprimer."""
    headers = await _login(client, demo_org)
    choices = (await client.get("/api/v1/demarrage/choix", headers=headers)).json()

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        before = (
            await session.execute(text("SELECT count(*) FROM app.fields"))
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO app.plans (code, name_fr, description_fr, max_fields, "
                "sort_order, is_active) VALUES ('TEST_PLEIN', 'Plein', 'Plan de test', "
                "1, 98, true) ON CONFLICT (code) DO NOTHING"
            )
        )
        await session.execute(
            text("UPDATE app.tenants SET plan_code = 'TEST_PLEIN' WHERE id = :t"),
            {"t": demo_org.tenant_id},
        )
    try:
        response = await client.post(
            "/api/v1/parcelles", json=_payload("N04", choices), headers=headers
        )
        assert response.status_code == 429, response.text
        assert "Plafond atteint" in response.text
        async with databases.for_tenant(demo_org.tenant_id).begin() as session:
            after = (
                await session.execute(text("SELECT count(*) FROM app.fields"))
            ).scalar_one()
        assert after == before, "aucune parcelle n'a été écrite avant le refus"
    finally:
        async with databases.for_tenant(demo_org.tenant_id).begin() as session:
            await session.execute(
                text("UPDATE app.tenants SET plan_code = 'COOPERATIVE' WHERE id = :t"),
                {"t": demo_org.tenant_id},
            )
            await session.execute(
                text("DELETE FROM app.plans WHERE code = 'TEST_PLEIN'")
            )


async def test_a_created_field_belongs_to_its_organisation_alone(
    client: AsyncClient, databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    """Une parcelle créée par alpha ne doit pas apparaître chez beta."""
    alpha, beta = two_orgs
    # Alpha a besoin d'un site et d'un référentiel : on lui donne le minimum.
    headers = await _login(client, alpha)
    site = await client.post(
        "/api/v1/sites",
        json={
            "code": "F01",
            "name_fr": "Ferme alpha",
            "site_type": "FARM",
            "latitude": 30.4,
            "longitude": -9.6,
        },
        headers=headers,
    )
    assert site.status_code == 201, site.text
    choices = (await client.get("/api/v1/demarrage/choix", headers=headers)).json()
    created = await client.post(
        "/api/v1/parcelles", json=_payload("A01", choices), headers=headers
    )
    assert created.status_code == 201, created.text

    beta_headers = await _login(client, beta)
    visible = (await client.get("/api/v1/parcelles", headers=beta_headers)).json()
    assert all(field["code"] != "A01" for field in visible)
