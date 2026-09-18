"""Plans, plafonds et mesure de l'usage.

Un quota se trompe toujours en silence. Il laisse passer une unité de trop, ou
il refuse un acte qui était dû, et personne ne s'en aperçoit avant qu'un client
compte lui-même. Ce fichier fixe donc les quatre décisions dont dépend tout le
reste :

* un **stock** se compte en lignes vivantes, un **flux** en événements de la
  période — les confondre produit deux défauts opposés ;
* `used >= limit` refuse, parce que `used` est la consommation *avant* l'acte ;
* `None` veut dire **illimité**, jamais zéro ;
* la dépense est vérifiée à côté du nombre d'appels, parce qu'une organisation
  peut tenir dans ses appels et sortir de son budget.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.errors import QuotaExceededError
from app.db.base import UsageEvent
from app.db.session import Databases
from app.domain.enums import UserRole
from app.domain.quotas import QuotaKind, UsageMetric, check_quota
from app.services.quota_service import QuotaService, period_start
from tests.conftest import Org

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés sans marqueur.


async def _login(client: AsyncClient, org: Org) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login", json={"email": org.email, "password": org.password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _set_plan(databases: Databases, org: Org, code: str) -> None:
    async with databases.for_tenant(org.tenant_id).begin() as session:
        await session.execute(
            text("UPDATE app.tenants SET plan_code = :c WHERE id = :t"),
            {"c": code, "t": org.tenant_id},
        )


async def _consume(
    databases: Databases,
    org: Org,
    metric: UsageMetric,
    times: int,
    *,
    cost_usd: float | None = 0.0,
    occurred_at: datetime | None = None,
) -> None:
    async with databases.for_tenant(org.tenant_id).begin() as session:
        for _ in range(times):
            event = UsageEvent(
                id=uuid.uuid4(),
                tenant_id=org.tenant_id,
                metric=metric,
                quantity=1,
                cost_usd=cost_usd,
                model="test-model",
            )
            if occurred_at is not None:
                event.occurred_at = occurred_at
            session.add(event)


# ---------------------------------------------------------------------------
# L'arithmétique, sans base
# ---------------------------------------------------------------------------
def test_equality_refuses_because_used_is_the_consumption_before_the_act() -> None:
    """À 100 sur 100, le 101e acte n'est pas dû.

    `used > limit` aurait vendu une unité de plus que le plan n'en contient, à
    chaque plafond et à chaque période.
    """
    at_limit = check_quota(
        label_fr="Questions",
        used=100,
        limit=100,
        unit_fr="questions",
        plan_name_fr="Coopérative",
        kind=QuotaKind.FLOW,
    )
    assert at_limit.allowed is False
    assert at_limit.remaining == 0

    below = check_quota(
        label_fr="Questions",
        used=99,
        limit=100,
        unit_fr="questions",
        plan_name_fr="Coopérative",
        kind=QuotaKind.FLOW,
    )
    assert below.allowed is True
    assert below.remaining == 1


def test_none_means_unlimited_and_not_a_total_ban() -> None:
    """Le plan Entreprise n'a pas de plafond de parcelles. Zéro l'aurait interdit."""
    decision = check_quota(
        label_fr="Parcelles",
        used=4_000,
        limit=None,
        unit_fr="parcelles",
        plan_name_fr="Entreprise",
        kind=QuotaKind.STOCK,
    )
    assert decision.allowed is True
    assert decision.unlimited is True
    assert decision.remaining is None
    assert decision.fraction is None, "une jauge sans dénominateur ne se dessine pas"


def test_a_refusal_names_the_plan_the_amount_and_the_remedy() -> None:
    """« Quota atteint » n'apprend rien à qui ignore quel plan il a."""
    decision = check_quota(
        label_fr="Parcelles suivies",
        used=25,
        limit=25,
        unit_fr="parcelles",
        plan_name_fr="Coopérative",
        kind=QuotaKind.STOCK,
    )
    assert decision.message_fr is not None
    assert "Coopérative" in decision.message_fr
    assert "25 parcelles" in decision.message_fr
    assert decision.remedy_fr is not None
    assert "administrateur" in decision.remedy_fr


def test_the_remedy_differs_between_a_stock_and_a_flow() -> None:
    """Supprimer une parcelle libère du stock. Supprimer un message ne rend rien."""
    stock = check_quota(
        label_fr="Parcelles",
        used=1,
        limit=1,
        unit_fr="parcelles",
        plan_name_fr="Coopérative",
        kind=QuotaKind.STOCK,
    )
    flow = check_quota(
        label_fr="Questions",
        used=1,
        limit=1,
        unit_fr="questions",
        plan_name_fr="Coopérative",
        kind=QuotaKind.FLOW,
    )
    assert stock.remedy_fr is not None and "Supprimez" in stock.remedy_fr
    assert flow.remedy_fr is not None and "mois prochain" in flow.remedy_fr


def test_a_count_is_written_without_a_decimal_and_a_spend_with_two() -> None:
    """« 100,0 questions » se lit comme une mesure. C'en est un décompte."""
    count = check_quota(
        label_fr="Questions",
        used=100.0,
        limit=100,
        unit_fr="questions",
        plan_name_fr="Coopérative",
        kind=QuotaKind.FLOW,
    )
    spend = check_quota(
        label_fr="Dépense",
        used=5.0,
        limit=5.0,
        unit_fr="USD",
        plan_name_fr="Coopérative",
        kind=QuotaKind.FLOW,
    )
    assert count.message_fr is not None and "100 questions" in count.message_fr
    assert spend.message_fr is not None and "5.00 USD" in spend.message_fr


def test_the_unit_agrees_in_number() -> None:
    """« 1 expéditions » fait ressembler un produit professionnel à un brouillon.

    Le singulier vaut sous deux — « 0 expédition » est correct en français — et
    une abréviation comme USD ne s'accorde pas.
    """
    one = check_quota(
        label_fr="Expéditions",
        used=1,
        limit=1,
        unit_fr="expéditions",
        plan_name_fr="Coopérative",
        kind=QuotaKind.STOCK,
    )
    assert one.message_fr is not None
    assert "1 expédition sur 1 expédition" in one.message_fr

    two = check_quota(
        label_fr="Expéditions",
        used=2,
        limit=2,
        unit_fr="expéditions",
        plan_name_fr="Coopérative",
        kind=QuotaKind.STOCK,
    )
    assert two.message_fr is not None
    assert "2 expéditions sur 2 expéditions" in two.message_fr

    spend = check_quota(
        label_fr="Dépense",
        used=1.0,
        limit=1.0,
        unit_fr="USD",
        plan_name_fr="Coopérative",
        kind=QuotaKind.FLOW,
    )
    assert spend.message_fr is not None and "1.00 USD" in spend.message_fr


def test_the_period_starts_on_the_first_of_the_calendar_month() -> None:
    """Un mois calendaire se vérifie sur un calendrier. Pas une fenêtre glissante."""
    start = period_start(datetime(2026, 3, 18, 14, 32, tzinfo=UTC))
    assert start == datetime(2026, 3, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Le comptage, contre la vraie base
# ---------------------------------------------------------------------------
async def test_a_stock_is_counted_in_live_rows_and_a_flow_in_events(
    databases: Databases, demo_org: Org
) -> None:
    """Les deux grandeurs ne se comptent pas au même endroit, et c'est voulu.

    Le stock des parcelles vient de `app.fields` : pas de second compteur, donc
    pas de seconde vérité. Le flux vient des événements : un message consommé
    reste consommé.
    """
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        live_fields = (
            await session.execute(text("SELECT count(*) FROM app.fields"))
        ).scalar_one()
        snapshot = await QuotaService(session, demo_org.context).snapshot()

    assert snapshot.fields.used == live_fields
    assert live_fields > 0, "le jeu de démonstration porte des parcelles"
    assert snapshot.agent_messages.used == 0, "aucun événement, donc aucun flux"

    await _consume(databases, demo_org, UsageMetric.AGENT_MESSAGE, 3)

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        after = await QuotaService(session, demo_org.context).snapshot()
    assert after.agent_messages.used == 3
    assert after.fields.used == live_fields, "un message ne consomme pas de parcelle"


async def test_an_event_of_last_month_does_not_count_this_month(
    databases: Databases, demo_org: Org
) -> None:
    """Le compteur repart le 1er. Un flux qui n'expire jamais n'est pas mensuel."""
    last_month = period_start() - timedelta(days=1)
    await _consume(
        databases, demo_org, UsageMetric.AGENT_MESSAGE, 5, occurred_at=last_month
    )
    await _consume(databases, demo_org, UsageMetric.AGENT_MESSAGE, 2)

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        snapshot = await QuotaService(session, demo_org.context).snapshot()
    assert snapshot.agent_messages.used == 2


async def test_two_organisations_do_not_share_a_counter(
    databases: Databases, two_orgs: tuple[Org, Org]
) -> None:
    """Un quota qui fuit d'une organisation à l'autre facture l'une pour l'autre."""
    alpha, beta = two_orgs
    await _consume(databases, alpha, UsageMetric.ANALYTICS_QUERY, 4)

    async with databases.for_tenant(alpha.tenant_id).begin() as session:
        alpha_snapshot = await QuotaService(session, alpha.context).snapshot()
    async with databases.for_tenant(beta.tenant_id).begin() as session:
        beta_snapshot = await QuotaService(session, beta.context).snapshot()

    assert alpha_snapshot.analytics_queries.used == 4
    assert beta_snapshot.analytics_queries.used == 0


async def test_an_unpriced_call_makes_the_spend_a_lower_bound(
    databases: Databases, demo_org: Org
) -> None:
    """Additionner `NULL` comme zéro présenterait un total partiel comme complet."""
    await _consume(databases, demo_org, UsageMetric.AGENT_MESSAGE, 1, cost_usd=0.25)
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        priced = await QuotaService(session, demo_org.context).snapshot()
    assert priced.llm_spend.used == pytest.approx(0.25)
    assert priced.spend_is_partial is False

    await _consume(databases, demo_org, UsageMetric.AGENT_MESSAGE, 1, cost_usd=None)
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        partial = await QuotaService(session, demo_org.context).snapshot()
    assert partial.llm_spend.used == pytest.approx(0.25), "l'appel non tarifé n'ajoute rien"
    assert partial.spend_is_partial is True


# ---------------------------------------------------------------------------
# L'application, avant l'acte
# ---------------------------------------------------------------------------
async def test_require_refuses_once_the_month_is_spent(
    databases: Databases, demo_org: Org
) -> None:
    """Le plan Coopérative vend 100 questions au copilote. La 101e est refusée."""
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        plan = await QuotaService(session, demo_org.context).plan()
    limit = plan.max_agent_messages_per_month
    assert limit is not None, "le plan de démonstration doit porter un plafond"

    await _consume(databases, demo_org, UsageMetric.AGENT_MESSAGE, limit - 1)
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        await QuotaService(session, demo_org.context).require(UsageMetric.AGENT_MESSAGE)

    await _consume(databases, demo_org, UsageMetric.AGENT_MESSAGE, 1)
    with pytest.raises(QuotaExceededError) as raised:
        async with databases.for_tenant(demo_org.tenant_id).begin() as session:
            await QuotaService(session, demo_org.context).require(
                UsageMetric.AGENT_MESSAGE
            )
    assert "Plafond atteint" in str(raised.value)
    assert raised.value.http_status == 429


async def test_one_metric_being_spent_does_not_block_the_other(
    databases: Databases, demo_org: Org
) -> None:
    """Deux compteurs distincts, ou bien le copilote ferme l'analyse."""
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        plan = await QuotaService(session, demo_org.context).plan()
    assert plan.max_agent_messages_per_month is not None
    await _consume(
        databases, demo_org, UsageMetric.AGENT_MESSAGE, plan.max_agent_messages_per_month
    )

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        await QuotaService(session, demo_org.context).require(
            UsageMetric.ANALYTICS_QUERY
        )


async def test_the_budget_is_checked_beside_the_call_count(
    databases: Databases, demo_org: Org
) -> None:
    """Une question longue coûte davantage qu'une courte.

    Rester dans son nombre d'appels ne prouve donc pas qu'on est dans son budget,
    et un plafond de dépense qui n'est vérifié qu'à la fin du mois arrive après
    la dépense.
    """
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        plan = await QuotaService(session, demo_org.context).plan()
    budget = plan.max_llm_spend_usd_per_month
    assert budget is not None

    # Deux appels seulement : très loin du plafond d'appels, mais tout le budget.
    await _consume(
        databases, demo_org, UsageMetric.AGENT_MESSAGE, 2, cost_usd=budget / 2
    )

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        snapshot = await QuotaService(session, demo_org.context).snapshot()
    assert snapshot.agent_messages.allowed is True, "le nombre d'appels reste loin du plafond"

    with pytest.raises(QuotaExceededError) as raised:
        async with databases.for_tenant(demo_org.tenant_id).begin() as session:
            await QuotaService(session, demo_org.context).require(
                UsageMetric.AGENT_MESSAGE
            )
    assert "dépense du modèle" in str(raised.value)


async def test_usage_is_recorded_after_the_act_not_before(
    databases: Databases, demo_org: Org
) -> None:
    """Facturer une panne est la façon la plus sûre de discréditer un compteur.

    `require` lit ; seul `record` écrit. Le test le prouve en vérifiant qu'une
    vérification seule ne laisse aucun événement derrière elle.
    """
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        service = QuotaService(session, demo_org.context)
        await service.require(UsageMetric.AGENT_MESSAGE)
        await service.require(UsageMetric.ANALYTICS_QUERY)

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        assert (await QuotaService(session, demo_org.context).snapshot()).agent_messages.used == 0

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        await QuotaService(session, demo_org.context).record(
            UsageMetric.AGENT_MESSAGE,
            input_tokens=1_200,
            output_tokens=300,
            cost_usd=0.012,
            model="test-model",
        )

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        snapshot = await QuotaService(session, demo_org.context).snapshot()
    assert snapshot.agent_messages.used == 1
    assert snapshot.llm_spend.used == pytest.approx(0.012)


async def test_real_tokens_and_derived_cost_live_in_separate_columns(
    databases: Databases, demo_org: Org
) -> None:
    """Les jetons viennent de l'API ; le coût d'une grille recopiée.

    Une seule colonne « coût » aurait laissé présenter une estimation comme une
    mesure — exactement ce que le grand livre d'honnêteté interdit.
    """
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        await QuotaService(session, demo_org.context).record(
            UsageMetric.ANALYTICS_QUERY,
            input_tokens=900,
            output_tokens=120,
            cost_usd=None,
            model="modele-non-tarife",
        )

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        row = (
            await session.execute(
                text(
                    "SELECT input_tokens, output_tokens, cost_usd FROM app.usage_events"
                )
            )
        ).one()
    assert row[0] == 900
    assert row[1] == 120
    assert row[2] is None, "non tarifé n'est pas gratuit"


async def test_the_database_refuses_a_tenant_pointing_at_no_plan(
    databases: Databases, demo_org: Org
) -> None:
    """L'absence de plan n'est pas l'illimité, et c'est la base qui le garantit.

    Une organisation sans plan n'aurait aucun plafond vérifiable. Plutôt que de
    compter sur le service pour le remarquer, la clé étrangère
    `fk_tenants_plan_code` rend la situation impossible à écrire — une garantie
    structurelle, pas un vœu.

    Le service porte tout de même sa propre vérification : elle couvre le cas
    d'un schéma où la contrainte aurait été retirée, et elle **ferme** la porte
    au lieu de l'ouvrir.
    """
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with databases.for_tenant(demo_org.tenant_id).begin() as session:
            await session.execute(
                text("UPDATE app.tenants SET plan_code = 'PLAN_DISPARU' WHERE id = :t"),
                {"t": demo_org.tenant_id},
            )

    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        assert (await QuotaService(session, demo_org.context).plan()).code


# ---------------------------------------------------------------------------
# La surface HTTP
# ---------------------------------------------------------------------------
async def test_the_subscription_screen_shows_every_gauge_with_its_denominator(
    client: AsyncClient, demo_org: Org
) -> None:
    """« 87 » ne dit rien. « 87 sur 100 » dit qu'il en reste treize."""
    headers = await _login(client, demo_org)
    response = await client.get("/api/v1/abonnement", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["plan"]["code"]
    assert {p["code"] for p in body["available_plans"]} >= {
        "COOPERATIVE",
        "EXPLOITATION",
        "ENTREPRISE",
    }
    for key in ("fields", "shipments", "agent_messages", "analytics_queries", "llm_spend"):
        gauge = body[key]
        assert gauge["label_fr"], key
        assert gauge["unit_fr"], key
        assert "used" in gauge and "limit" in gauge, key
    assert body["spend_notice_fr"], "une dépense dérivée doit le dire"
    assert "facture" in body["spend_notice_fr"]


async def test_changing_plan_is_reserved_to_an_administrator(
    client: AsyncClient, databases: Databases, demo_org: Org
) -> None:
    """Un plan décide de ce que l'organisation a le droit de consommer."""
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        await session.execute(
            text("UPDATE app.users SET role = :r WHERE tenant_id = :t"),
            {"r": UserRole.AGRONOME.value, "t": demo_org.tenant_id},
        )
    headers = await _login(client, demo_org)
    response = await client.post(
        "/api/v1/abonnement/plan", json={"plan_code": "ENTREPRISE"}, headers=headers
    )
    assert response.status_code == 403, response.text
    assert "administrateur" in response.text


async def test_an_unknown_plan_is_refused_by_name(
    client: AsyncClient, demo_org: Org
) -> None:
    """Refuser sans nommer les plans possibles oblige à deviner."""
    headers = await _login(client, demo_org)
    response = await client.post(
        "/api/v1/abonnement/plan", json={"plan_code": "GRATUIT"}, headers=headers
    )
    assert response.status_code == 422, response.text
    assert "GRATUIT" in response.text
    assert "COOPERATIVE" in response.text


async def test_an_administrator_changes_plan_and_the_ceilings_follow(
    client: AsyncClient, demo_org: Org
) -> None:
    """Le changement de plan doit se voir tout de suite sur les jauges."""
    headers = await _login(client, demo_org)
    before = (await client.get("/api/v1/abonnement", headers=headers)).json()
    assert before["fields"]["limit"] is not None

    response = await client.post(
        "/api/v1/abonnement/plan", json={"plan_code": "ENTREPRISE"}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plan"]["code"] == "ENTREPRISE"
    assert body["fields"]["limit"] is None, "Entreprise est sans plafond de parcelles"
    assert body["fields"]["allowed"] is True


async def test_going_down_a_plan_keeps_the_data_and_refuses_the_next_creation(
    databases: Databases, demo_org: Org
) -> None:
    """Descendre de plan ne supprime rien.

    Faire tenir les données dans le nouveau plan serait une décision que
    personne n'a prise ; ce qui change, c'est la création **suivante**.
    """
    async with databases.for_tenant(demo_org.tenant_id).begin() as session:
        live_fields = (
            await session.execute(text("SELECT count(*) FROM app.fields"))
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO app.plans (code, name_fr, description_fr, max_fields, "
                "sort_order, is_active) VALUES ('TEST_MINUSCULE', 'Minuscule', "
                "'Plan de test', 1, 99, true) ON CONFLICT (code) DO NOTHING"
            )
        )
    assert live_fields > 1

    await _set_plan(databases, demo_org, "TEST_MINUSCULE")
    try:
        async with databases.for_tenant(demo_org.tenant_id).begin() as session:
            still_there = (
                await session.execute(text("SELECT count(*) FROM app.fields"))
            ).scalar_one()
            assert still_there == live_fields, "aucune parcelle n'a été supprimée"
            with pytest.raises(QuotaExceededError):
                await QuotaService(session, demo_org.context).check_stock("field")
    finally:
        await _set_plan(databases, demo_org, "COOPERATIVE")
        async with databases.for_tenant(demo_org.tenant_id).begin() as session:
            await session.execute(
                text("DELETE FROM app.plans WHERE code = 'TEST_MINUSCULE'")
            )
