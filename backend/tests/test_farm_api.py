"""La surface que l'interface consomme.

Ce fichier vérifie ce dont un écran dépend et qui, sinon, se dégrade en silence :
qu'une parcelle inutilisable reste visible avec son motif, qu'une capacité non
livrée est annoncée par le serveur plutôt que devinée par le client, et que la
recommandation arrive avec de quoi remplir les deux panneaux — sans que
l'interface ait à recalculer quoi que ce soit.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from tests.conftest import Org

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés sans marqueur.


async def _login(client: AsyncClient, org: Org) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": org.email, "password": org.password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_a_field_that_cannot_be_computed_stays_visible_with_its_reason(
    client: AsyncClient, demo_org: Org, databases: Any
) -> None:
    """Une parcelle sans culture reste dans la liste, en gris, avec le motif.

    La faire disparaître serait le pire comportement : une parcelle absente d'un
    écran est indiscernable d'une parcelle qui va bien.
    """
    import uuid

    from app.db.base import Field, Site

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        from sqlalchemy import select

        site = (await session.execute(select(Site))).scalars().first()
        assert site is not None
        session.add(
            Field(
                id=uuid.uuid4(), tenant_id=demo_org.tenant_id, site_id=site.id,
                code="P99", name_fr="Parcelle sans culture", area_ha=1.0,
                latitude=30.4, longitude=-9.2,
            )
        )

    headers = await _login(client, demo_org)
    response = await client.get("/api/v1/parcelles", headers=headers)
    assert response.status_code == 200, response.text
    fields = {f["code"]: f for f in response.json()}

    orphan = fields["P99"]
    assert orphan["blocked_reason_fr"] == "Aucune culture renseignée pour cette parcelle."
    assert orphan["crop_name_fr"] is None

    # Une parcelle complète ne porte aucun motif de blocage.
    assert fields["P03"]["blocked_reason_fr"] is None
    assert fields["P03"]["crop_name_fr"]


async def test_every_moisture_reading_arrives_with_its_provenance(
    client: AsyncClient, demo_org: Org
) -> None:
    """Le couple voyage jusqu'à l'écran.

    27 % relevé à la sonde et 27 % issu du jeu de démonstration sont le même
    nombre dans la même colonne ; seule cette paire les distingue.
    """
    headers = await _login(client, demo_org)
    fields = (await client.get("/api/v1/parcelles", headers=headers)).json()
    measured = [f for f in fields if f["moisture"] is not None]
    assert measured
    for field in measured:
        moisture = field["moisture"]
        assert moisture["state_label_fr"]
        assert moisture["origin_label_fr"]
        # Organisation de démonstration : rien ne se présente comme mesuré.
        assert moisture["state"] == "SIMULATED"
        assert moisture["origin"] == "SEED_DEMO"


async def test_the_recommendation_carries_both_panels(
    client: AsyncClient, demo_org: Org
) -> None:
    """« Pourquoi cette décision ? » et « Sources et preuves » se remplissent
    depuis la réponse, sans recalcul côté interface.

    Si un chiffre affiché n'était pas dans cette charge utile, l'écran devrait
    l'inventer — et un même chiffre finirait par différer entre l'écran et l'API.
    """
    headers = await _login(client, demo_org)
    response = await client.get("/api/v1/parcelles/P03/irrigation", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["field_code"] == "P03"
    assert body["recommendation"] in {"IRRIGATE", "MONITOR", "NO_IRRIGATION", "POSTPONE_RAIN"}
    assert body["recommendation_label_fr"]
    assert body["stress_label_fr"] in {
        "Normal", "Stress modéré", "Stress élevé", "Stress critique"
    }

    decision = body["decision"]
    # Panneau 1 : entrées avec provenance, étapes numérotées, hypothèses.
    assert decision["inputs"]
    for entry in decision["inputs"]:
        assert entry["state_label_fr"] and entry["origin_label_fr"]
    assert decision["calculation_steps_fr"]
    # Le numéro d'équation FAO est cité là où il y en a un.
    assert any("éq." in step for step in decision["calculation_steps_fr"])
    # Panneau 2 : preuves en langage métier.
    assert decision["evidence"]
    for item in decision["evidence"]:
        assert item["label_fr"] and item["detail_fr"]


async def test_a_missing_output_is_declared_not_substituted(
    client: AsyncClient, demo_org: Org
) -> None:
    """Ce qui manque manque, et le dit — jusqu'à l'écran.

    Sans tarif de l'eau il n'y a pas de coût ; le champ vaut `null` et
    `unavailable_fr` porte la raison. Un zéro se lirait comme « gratuit ».
    """
    headers = await _login(client, demo_org)
    body = (
        await client.get("/api/v1/parcelles/P03/irrigation", headers=headers)
    ).json()

    for key in ("duration_minutes", "estimated_cost_mad"):
        if body[key] is None:
            assert body["decision"]["unavailable_fr"], (
                f"{key} est absent sans raison déclarée"
            )
    if body["duration_minutes"] is not None:
        assert body["duration_label_fr"]


async def test_an_undelivered_capability_is_announced_by_the_server(
    client: AsyncClient, demo_org: Org
) -> None:
    """L'interface n'invente pas ce que la plateforme ne sait pas faire.

    La liste rétrécit à mesure que les moteurs arrivent — c'est le point : elle
    est rendue par le serveur, donc l'écran cesse d'annoncer une absence sans
    être modifié. Le risque logistique en est sorti quand le moteur a été livré ;
    les stocks et l'analyse en langage naturel y restent.
    """
    headers = await _login(client, demo_org)
    overview = (await client.get("/api/v1/vue-generale", headers=headers)).json()
    assert overview["not_delivered_fr"]
    assert any("Stocks" in line for line in overview["not_delivered_fr"])
    assert not any("risque logistique" in line for line in overview["not_delivered_fr"])
    # L'analyse en langage naturel en est sortie à son tour.
    assert not any("langage naturel" in line for line in overview["not_delivered_fr"])

    # Et une expédition ne porte plus de motif d'absence.
    shipments = (await client.get("/api/v1/expeditions", headers=headers)).json()
    assert shipments
    assert all(s["risk_analysis_fr"] is None for s in shipments)


async def test_the_shipment_risk_analysis_runs_end_to_end(
    client: AsyncClient, demo_org: Org
) -> None:
    """L'analyse complète, telle que l'écran la lit.

    Ce test remplace celui qui constatait l'absence du moteur : la dette de la
    phase 3 est comblée, et la suite doit désormais échouer si l'analyse
    disparaît, pas si elle apparaît.
    """
    headers = await _login(client, demo_org)
    response = await client.get("/api/v1/expeditions/EXP-1842/risque", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["reference"] == "EXP-1842"
    assert body["headline_fr"]
    assert body["risk_level"] in {"LOW", "MODERATE", "HIGH", "CRITICAL"}
    assert body["profile_fr"] == "Denrée périssable"
    assert body["profile_rationale_fr"]

    # L'indicateur ne voyage jamais sans sa mise en garde.
    assert 0.0 <= body["disruption_indicator"] <= 0.95
    assert "pas une probabilité" in body["disruption_caveat_fr"]

    # Le plan actuel sert de référence ; les écarts sont relatifs à lui.
    alternatives = body["alternatives"]
    assert alternatives
    current = [a for a in alternatives if a["is_current_plan"]]
    assert len(current) == 1
    assert current[0]["cost_delta_mad"] == 0

    # Au moins une option de repli a été examinée.
    assert any(a["kind"] == "DEPARTURE_SHIFT" for a in alternatives)

    # Le classement porte sur les seules options faisables.
    feasible = [a for a in alternatives if a["is_feasible"]]
    assert feasible
    assert sorted(a["rank"] for a in feasible) == list(range(1, len(feasible) + 1))
    assert sum(1 for a in alternatives if a["is_recommended"]) == 1


async def test_a_rejected_option_carries_its_numbers_and_no_others(
    client: AsyncClient, demo_org: Org
) -> None:
    """« Infaisable » ne se conteste pas ; « 1 h < 3 h requises » se conteste.

    Et une option écartée ne porte aucun chiffre de comparaison : les renseigner
    la ferait figurer dans le tableau comme un choix possible.
    """
    headers = await _login(client, demo_org)
    body = (
        await client.get("/api/v1/expeditions/EXP-1842/risque", headers=headers)
    ).json()

    rejected = [a for a in body["alternatives"] if not a["is_feasible"]]
    for option in rejected:
        assert option["rejection_reasons_fr"], option["id"]
        assert option["cost_mad"] is None
        assert option["duration_hours"] is None
        assert option["risk_level"] is None
        assert option["rank"] is None
        assert option["is_recommended"] is False


async def test_the_analysis_declares_what_it_is_not(
    client: AsyncClient, demo_org: Org
) -> None:
    """Les limites voyagent avec le chiffre, jusqu'à l'écran.

    Un indicateur non calibré présenté sans sa réserve devient une probabilité
    dans la tête du lecteur, et c'est irréversible.
    """
    headers = await _login(client, demo_org)
    body = (
        await client.get("/api/v1/expeditions/EXP-1842/risque", headers=headers)
    ).json()
    warnings = body["decision"]["warnings_fr"]
    assert any("pas une probabilité calibrée" in w for w in warnings)
    assert any("valeurs de départ" in w for w in warnings)
    # Jeu de démonstration : la fiabilité ne peut pas être élevée.
    assert body["reliability_label_fr"] != "Élevée"


async def test_a_shipment_from_another_organisation_has_no_analysis(
    client: AsyncClient, demo_org: Org, two_orgs: tuple[Org, Org]
) -> None:
    """L'isolation traverse aussi la route de risque."""
    alpha, _ = two_orgs
    theirs = await _login(client, alpha)
    response = await client.get("/api/v1/expeditions/EXP-1842/risque", headers=theirs)
    assert response.status_code == 404
    assert "introuvable" in response.json()["message_fr"]


async def test_the_overview_counts_agree_with_the_field_pages(
    client: AsyncClient, demo_org: Org
) -> None:
    """L'accueil et les fiches sortent du même calcul.

    Un décompte d'accueil obtenu par un raccourci — un seuil sur la dernière
    humidité, par exemple — finirait par contredire la fiche qui, elle, lance le
    moteur. Ici les deux appellent le même service, et le test le vérifie
    parcelle par parcelle.
    """
    headers = await _login(client, demo_org)
    overview = (await client.get("/api/v1/vue-generale", headers=headers)).json()
    fields = (await client.get("/api/v1/parcelles", headers=headers)).json()

    assert overview["fields_total"] == len(fields)
    assert overview["fields_blocked"] == sum(
        1 for f in fields if f["blocked_reason_fr"] is not None
    )
    assert overview["is_demo"] is True

    computed = 0
    for field in fields:
        if field["blocked_reason_fr"] is not None:
            continue
        body = (
            await client.get(
                f"/api/v1/parcelles/{field['code']}/irrigation", headers=headers
            )
        ).json()
        if body["recommendation"] == "IRRIGATE":
            computed += 1
    assert overview["fields_to_irrigate"] == computed


async def test_a_field_from_another_organisation_is_not_reachable(
    client: AsyncClient, demo_org: Org, two_orgs: tuple[Org, Org]
) -> None:
    """L'isolation traverse la nouvelle surface comme le reste.

    P03 existe. Elle est simplement invisible depuis une autre organisation, et
    la réponse ne distingue pas « pas à vous » de « n'existe pas » : sinon l'API
    confirme l'existence d'une ressource d'autrui.
    """
    alpha, _ = two_orgs
    mine = await _login(client, demo_org)
    theirs = await _login(client, alpha)

    assert (
        await client.get("/api/v1/parcelles/P03/irrigation", headers=mine)
    ).status_code == 200
    other = await client.get("/api/v1/parcelles/P03/irrigation", headers=theirs)
    assert other.status_code == 404
    assert "introuvable" in other.json()["message_fr"]


async def test_the_shipment_links_back_to_the_field_that_grew_it(
    client: AsyncClient, demo_org: Org
) -> None:
    """Le lien qui rend la démonstration cohérente d'un domaine à l'autre."""
    headers = await _login(client, demo_org)
    shipment = (
        await client.get("/api/v1/expeditions/EXP-1842", headers=headers)
    ).json()
    assert shipment["source_field_code"]
    assert "Casablanca" in shipment["destination_site_fr"]
    assert shipment["requires_cold_chain"] is True
