"""Les recommandations, et ce que l'humain en a fait.

Ce fichier existe parce qu'un outil disait `recorded=true` sans rien écrire. Le
modèle annonçait à l'utilisateur que sa décision était consignée, l'utilisateur
le croyait, et rien n'existait. Les tests portent donc d'abord sur l'effet réel,
puis sur les invariants que la table doit tenir des mois plus tard.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.errors import ValidationError
from app.db.base import Recommendation
from app.db.session import Databases
from app.domain.decision import HumanVerdict
from app.domain.enums import DataOrigin, DataState
from app.services.recommendation_service import RecommendationService
from tests.conftest import Org

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés sans marqueur.


async def _login(client: AsyncClient, org: Org) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": org.email, "password": org.password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _record(databases: Databases, org: Org, code: str = "P03") -> uuid.UUID:
    """Enregistre une vraie recommandation, par le vrai chemin."""
    from app.adapters.weather import build_weather_provider
    from app.services.irrigation_service import IrrigationService

    async with databases.for_tenant(org.tenant_id).begin() as session:
        outcome = await IrrigationService(
            session, build_weather_provider("offline")
        ).decide(code)
        row = await RecommendationService(session, org.context).record(
            outcome.decision,
            rationale_fr="Déficit projeté au-dessus du seuil.",
            data_state=DataState.DERIVED,
            data_origin=DataOrigin.SEED_DEMO,
        )
        return row.id


# ---------------------------------------------------------------------------
# L'effet réel
# ---------------------------------------------------------------------------
async def test_the_tool_now_actually_writes_a_row(
    databases: Databases, demo_org: Org
) -> None:
    """`recorded=true` doit correspondre à une ligne en base.

    Un outil qui se trompe sur son propre effet est pire qu'un outil absent.
    """
    from app.tools import business_tools  # noqa: F401 - enregistre les outils
    from app.tools.registry import ToolContext, registry

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        payload, failed = await registry.execute(
            "create_recommendation",
            {
                "subject_id": "P03",
                "headline_fr": "Irriguer P03",
                "rationale_fr": "Le déficit projeté dépasse le seuil de déclenchement.",
                "domain": "IRRIGATION",
            },
            ToolContext(session=session, request=demo_org.context),
        )
    assert failed is False, payload
    assert payload["recorded"] is True
    assert payload["verdict"] == "PENDING"
    recorded_id = uuid.UUID(payload["recommendation_id"])

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        row = (
            await session.execute(
                select(Recommendation).where(Recommendation.id == recorded_id)
            )
        ).scalar_one()
    assert row.subject_id == "P03"
    assert row.verdict is HumanVerdict.PENDING


async def test_the_stored_decision_is_the_engine_output_not_the_model_prose(
    databases: Databases, demo_org: Org
) -> None:
    """Le `payload` porte la décision calculée, avec ses étapes et sa provenance.

    Conserver la phrase du modèle sous le nom de décision ferait que la question
    « qu'avions-nous conseillé ? » recevrait une réponse inventée des mois plus
    tard.
    """
    recommendation_id = await _record(databases, demo_org)
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        row = (
            await session.execute(
                select(Recommendation).where(Recommendation.id == recommendation_id)
            )
        ).scalar_one()

    payload = row.payload
    assert payload["inputs"], "la décision figée a perdu ses entrées"
    assert payload["calculation_steps_fr"]
    assert any("éq." in step for step in payload["calculation_steps_fr"])
    for entry in payload["inputs"]:
        assert entry["state_label_fr"] and entry["origin_label_fr"]


async def test_the_recommendation_is_no_more_reliable_than_its_weakest_input(
    databases: Databases, demo_org: Org
) -> None:
    """Une proposition tirée d'un jeu de démonstration s'archive comme telle.

    Retenir la meilleure origine — ou une valeur par défaut — ferait qu'une
    proposition fondée sur une humidité simulée s'enregistrerait comme fondée
    sur une mesure.
    """
    from app.tools import business_tools  # noqa: F401
    from app.tools.registry import ToolContext, registry

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        payload, failed = await registry.execute(
            "create_recommendation",
            {
                "subject_id": "P03",
                "headline_fr": "Irriguer P03",
                "rationale_fr": "Déficit projeté au-dessus du seuil.",
                "domain": "IRRIGATION",
            },
            ToolContext(session=session, request=demo_org.context),
        )
        assert failed is False
        row = (
            await session.execute(
                select(Recommendation).where(
                    Recommendation.id == uuid.UUID(payload["recommendation_id"])
                )
            )
        ).scalar_one()
    assert row.data_origin is DataOrigin.SEED_DEMO


# ---------------------------------------------------------------------------
# Le verdict
# ---------------------------------------------------------------------------
async def test_a_verdict_carries_who_and_when(
    databases: Databases, demo_org: Org
) -> None:
    recommendation_id = await _record(databases, demo_org)
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        row = await RecommendationService(session, demo_org.context).decide(
            recommendation_id, verdict=HumanVerdict.ACCEPTED, note_fr="Appliqué le matin."
        )
        assert row.verdict is HumanVerdict.ACCEPTED
        assert row.decided_at is not None
        assert row.decided_by == demo_org.context.user_id


async def test_a_verdict_is_rendered_once(
    databases: Databases, demo_org: Org
) -> None:
    """Écraser un verdict effacerait qui avait décidé quoi.

    C'est précisément ce que la table existe pour conserver : une décision qui
    change est une nouvelle recommandation.
    """
    recommendation_id = await _record(databases, demo_org)
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        service = RecommendationService(session, demo_org.context)
        await service.decide(recommendation_id, verdict=HumanVerdict.ACCEPTED)
        with pytest.raises(ValidationError) as excinfo:
            await service.decide(recommendation_id, verdict=HumanVerdict.REJECTED)
    assert "déjà été traitée" in str(excinfo.value)


async def test_pending_is_not_a_verdict(
    databases: Databases, demo_org: Org
) -> None:
    """« Annuler ma décision » effacerait la trace : ce n'est pas une opération."""
    recommendation_id = await _record(databases, demo_org)
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        with pytest.raises(ValidationError):
            await RecommendationService(session, demo_org.context).decide(
                recommendation_id, verdict=HumanVerdict.PENDING
            )


async def test_the_database_refuses_an_unattributed_verdict(
    databases: Databases, demo_org: Org
) -> None:
    """La contrainte vit dans la table, pas seulement dans le service.

    Une recommandation « acceptée » sans décideur ni horodatage est exactement
    la ligne qu'un audit cherche. Le service l'empêche ; la table doit
    l'empêcher aussi, parce qu'un second chemin d'écriture arrivera.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    recommendation_id = await _record(databases, demo_org)
    with pytest.raises(IntegrityError):
        async with databases.for_tenant(demo_org.tenant_id).begin() as session:
            await session.execute(
                text(
                    "UPDATE app.recommendations SET verdict = 'ACCEPTED' "
                    "WHERE id = :id"
                ),
                {"id": recommendation_id},
            )


# ---------------------------------------------------------------------------
# Le décompte que la §12 demande
# ---------------------------------------------------------------------------
async def test_the_closing_question_of_the_demonstration_can_be_answered(
    client: AsyncClient, databases: Databases, demo_org: Org
) -> None:
    """« Combien de recommandations ont été acceptées ? »"""
    first = await _record(databases, demo_org, "P03")
    second = await _record(databases, demo_org, "P01")
    await _record(databases, demo_org, "P02")

    headers = await _login(client, demo_org)
    for recommendation_id, verdict in ((first, "ACCEPTED"), (second, "REJECTED")):
        response = await client.post(
            f"/api/v1/recommandations/{recommendation_id}/verdict",
            headers=headers,
            json={"verdict": verdict},
        )
        assert response.status_code == 200, response.text

    counts = (
        await client.get("/api/v1/recommandations/decompte", headers=headers)
    ).json()
    assert counts["accepted"] == 1
    assert counts["rejected"] == 1
    assert counts["pending"] == 1
    assert counts["decided"] == 2
    assert counts["acceptance_rate"] == 0.5
    assert "50" in counts["acceptance_label_fr"]


async def test_an_acceptance_rate_on_nothing_is_absent_not_zero(
    client: AsyncClient, databases: Databases, demo_org: Org
) -> None:
    """0 % sur zéro décision se lirait comme « tout est refusé »."""
    await _record(databases, demo_org)
    headers = await _login(client, demo_org)
    counts = (
        await client.get("/api/v1/recommandations/decompte", headers=headers)
    ).json()
    assert counts["pending"] == 1
    assert counts["decided"] == 0
    assert counts["acceptance_rate"] is None
    assert "Aucune décision" in counts["acceptance_label_fr"]


async def test_the_list_carries_provenance_and_the_decider(
    client: AsyncClient, databases: Databases, demo_org: Org
) -> None:
    recommendation_id = await _record(databases, demo_org)
    headers = await _login(client, demo_org)
    await client.post(
        f"/api/v1/recommandations/{recommendation_id}/verdict",
        headers=headers,
        json={"verdict": "ACCEPTED", "note_fr": "Tour d'eau lancé à 6 h."},
    )

    rows = (await client.get("/api/v1/recommandations", headers=headers)).json()
    assert rows
    row = next(r for r in rows if r["id"] == str(recommendation_id))
    assert row["verdict_label_fr"] == "Acceptée"
    assert row["decided_by_name"]
    assert row["decision_note_fr"] == "Tour d'eau lancé à 6 h."
    assert row["provenance"]["state_label_fr"]
    assert row["provenance"]["origin"] == "SEED_DEMO"


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------
async def test_a_recommendation_from_another_organisation_is_unreachable(
    client: AsyncClient, databases: Databases, demo_org: Org, two_orgs: tuple[Org, Org]
) -> None:
    """Et un verdict rendu depuis une autre organisation est refusé.

    La réponse ne distingue pas « pas à vous » de « n'existe pas » : sinon l'API
    confirme l'existence d'une ressource d'autrui.
    """
    alpha, _ = two_orgs
    recommendation_id = await _record(databases, demo_org)

    theirs = await _login(client, alpha)
    listed = (await client.get("/api/v1/recommandations", headers=theirs)).json()
    assert listed == []

    response = await client.post(
        f"/api/v1/recommandations/{recommendation_id}/verdict",
        headers=theirs,
        json={"verdict": "ACCEPTED"},
    )
    assert response.status_code == 404
    assert "introuvable" in response.json()["message_fr"]


async def test_the_analytics_view_counts_verdicts_within_the_organisation(
    databases: Databases, demo_org: Org, two_orgs: tuple[Org, Org]
) -> None:
    """La voie analytique voit les mêmes verdicts, sous la même frontière."""
    from sqlalchemy import text

    alpha, _ = two_orgs
    recommendation_id = await _record(databases, demo_org)
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        await RecommendationService(session, demo_org.context).decide(
            recommendation_id, verdict=HumanVerdict.ACCEPTED
        )

    async with databases.analytics_for_tenant(demo_org.tenant_id).connection() as conn:
        mine = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM analytics.v_recommendations "
                    "WHERE verdict = 'ACCEPTED'"
                )
            )
        ).scalar_one()
    async with databases.analytics_for_tenant(alpha.tenant_id).connection() as conn:
        theirs = (
            await conn.execute(
                text("SELECT count(*) FROM analytics.v_recommendations")
            )
        ).scalar_one()

    assert mine == 1
    assert theirs == 0


async def test_the_analytics_view_exposes_no_free_text_payload(
    databases: Databases, demo_org: Org
) -> None:
    """`payload` n'est pas sur la surface analytique.

    Il contient la décision entière, y compris des libellés saisis par un
    exploitant. L'exposer mettrait du texte libre dans le chemin du résumeur sans
    raison métier : on compte des verdicts, on ne fouille pas des blobs.
    """
    from app.analytics.introspection import read_catalog

    async with databases.analytics_for_tenant(demo_org.tenant_id).connection() as conn:
        catalog = await read_catalog(conn)

    view = catalog.resolve_table("analytics.v_recommendations")
    assert view is not None
    assert "payload" not in view.column_names
    assert "rationale_fr" not in view.column_names
    assert "verdict" in view.column_names


async def test_the_client_sends_a_subject_never_a_decision(
    client: AsyncClient, demo_org: Org
) -> None:
    """La décision est recalculée par le moteur à l'enregistrement.

    Accepter une décision venue du navigateur laisserait archiver n'importe quel
    chiffre sous le nom du moteur, et « qu'avions-nous conseillé ? » perdrait
    toute valeur. Le schéma d'entrée n'a donc aucun champ pour la porter.
    """
    headers = await _login(client, demo_org)
    response = await client.post(
        "/api/v1/recommandations",
        headers=headers,
        json={"domain": "IRRIGATION", "subject_id": "P03"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["verdict"] == "PENDING"
    assert body["headline_fr"]
    assert body["subject_id"] == "P03"

    # Un client qui tenterait d'imposer la décision reçoit une erreur, pas un
    # champ ignoré en silence.
    forged = await client.post(
        "/api/v1/recommandations",
        headers=headers,
        json={
            "domain": "IRRIGATION",
            "subject_id": "P03",
            "headline_fr": "Ne rien faire",
            "payload": {},
        },
    )
    assert forged.status_code == 422
