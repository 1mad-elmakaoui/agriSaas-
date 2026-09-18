"""Vérification des quotas et enregistrement de l'usage.

Le service compte ; `app.domain.quotas` décide. La séparation n'est pas
cosmétique : l'arithmétique d'un plafond — « à égalité, on refuse », « `None`
veut dire illimité » — se teste sans base, et c'est là que les erreurs se
logent.

**Deux façons de compter, selon la nature.** Un stock s'obtient en comptant les
lignes vivantes de la table concernée. Un flux s'obtient en agrégeant les
événements du mois courant. Entretenir un compteur de parcelles à côté de
`app.fields` créerait deux vérités, et c'est toujours la seconde qu'on croit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import QuotaExceededError
from app.core.logging import get_logger
from app.core.security import RequestContext
from app.db.base import Field, Plan, Shipment, Tenant, UsageEvent
from app.domain.quotas import (
    PlanLimits,
    QuotaDecision,
    QuotaKind,
    UsageMetric,
    check_quota,
)

logger = get_logger(__name__)

__all__ = ["QuotaService", "UsageSnapshot"]


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """Tout ce qu'un écran d'abonnement doit montrer, en une lecture."""

    plan: PlanLimits
    fields: QuotaDecision
    shipments: QuotaDecision
    agent_messages: QuotaDecision
    analytics_queries: QuotaDecision
    llm_spend: QuotaDecision
    period_start: datetime
    #: `True` dès qu'un appel du mois n'est pas tarifé. La dépense affichée est
    #: alors un **minorant**, et l'écran doit le dire plutôt que de présenter un
    #: total partiel comme complet.
    spend_is_partial: bool


def period_start(now: datetime | None = None) -> datetime:
    """Début du mois courant, en UTC.

    Le mois calendaire plutôt qu'une fenêtre glissante : un exploitant sait
    quand son compteur repart, et « le 1er » est une réponse qu'il peut vérifier
    sur un calendrier.
    """
    moment = now or datetime.now(UTC)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class QuotaService:
    def __init__(self, session: AsyncSession, context: RequestContext) -> None:
        self._session = session
        self._context = context

    # -- lecture ------------------------------------------------------------

    async def plan(self) -> PlanLimits:
        tenant = (
            await self._session.execute(
                select(Tenant).where(Tenant.id == self._context.tenant_id)
            )
        ).scalar_one()
        row = (
            await self._session.execute(
                select(Plan).where(Plan.code == tenant.plan_code)
            )
        ).scalar_one_or_none()
        if row is None:
            # Une organisation dont le plan a disparu ne doit pas se retrouver
            # sans plafond : l'absence de plan n'est pas l'illimité.
            raise QuotaExceededError(
                f"Le plan « {tenant.plan_code} » de votre organisation est introuvable.",
                remedy_fr="Contactez votre administrateur : aucun plafond ne peut être vérifié.",
            )
        return PlanLimits(
            code=row.code,
            name_fr=row.name_fr,
            description_fr=row.description_fr,
            max_fields=row.max_fields,
            max_shipments=row.max_shipments,
            max_agent_messages_per_month=row.max_agent_messages_per_month,
            max_analytics_queries_per_month=row.max_analytics_queries_per_month,
            max_llm_spend_usd_per_month=row.max_llm_spend_usd_per_month,
        )

    async def snapshot(self, *, now: datetime | None = None) -> UsageSnapshot:
        plan = await self.plan()
        start = period_start(now)

        fields_used = await self._count_rows(Field)
        shipments_used = await self._count_rows(Shipment)
        messages_used = await self._count_events(UsageMetric.AGENT_MESSAGE, start)
        queries_used = await self._count_events(UsageMetric.ANALYTICS_QUERY, start)
        spend, partial = await self._spend(start)

        return UsageSnapshot(
            plan=plan,
            fields=check_quota(
                label_fr="Parcelles suivies",
                used=fields_used,
                limit=plan.max_fields,
                unit_fr="parcelles",
                plan_name_fr=plan.name_fr,
                kind=QuotaKind.STOCK,
            ),
            shipments=check_quota(
                label_fr="Expéditions enregistrées",
                used=shipments_used,
                limit=plan.max_shipments,
                unit_fr="expéditions",
                plan_name_fr=plan.name_fr,
                kind=QuotaKind.STOCK,
            ),
            agent_messages=check_quota(
                label_fr="Questions au copilote ce mois-ci",
                used=messages_used,
                limit=plan.max_agent_messages_per_month,
                unit_fr="questions",
                plan_name_fr=plan.name_fr,
                kind=QuotaKind.FLOW,
            ),
            analytics_queries=check_quota(
                label_fr="Questions d'analyse ce mois-ci",
                used=queries_used,
                limit=plan.max_analytics_queries_per_month,
                unit_fr="questions",
                plan_name_fr=plan.name_fr,
                kind=QuotaKind.FLOW,
            ),
            llm_spend=check_quota(
                label_fr="Dépense du modèle ce mois-ci",
                used=spend,
                limit=plan.max_llm_spend_usd_per_month,
                unit_fr="USD",
                plan_name_fr=plan.name_fr,
                kind=QuotaKind.FLOW,
            ),
            period_start=start,
            spend_is_partial=partial,
        )

    async def available_plans(self) -> list[PlanLimits]:
        rows = list(
            (
                await self._session.execute(
                    select(Plan).where(Plan.is_active).order_by(Plan.sort_order)
                )
            ).scalars()
        )
        return [
            PlanLimits(
                code=row.code,
                name_fr=row.name_fr,
                description_fr=row.description_fr,
                max_fields=row.max_fields,
                max_shipments=row.max_shipments,
                max_agent_messages_per_month=row.max_agent_messages_per_month,
                max_analytics_queries_per_month=row.max_analytics_queries_per_month,
                max_llm_spend_usd_per_month=row.max_llm_spend_usd_per_month,
            )
            for row in rows
        ]

    async def set_plan(self, plan_code: str) -> None:
        """Change le plan de l'organisation.

        Aucune vérification que la consommation actuelle tient dans le nouveau
        plan : descendre de plan avec trop de parcelles est une situation
        légitime — les parcelles existantes restent, la création suivante est
        refusée. Supprimer des données pour faire tenir un plan serait une
        décision que personne n'a prise.
        """
        tenant = (
            await self._session.execute(
                select(Tenant).where(Tenant.id == self._context.tenant_id)
            )
        ).scalar_one()
        tenant.plan_code = plan_code
        await self._session.flush()

    # -- application ---------------------------------------------------------

    async def require(self, metric: UsageMetric, *, now: datetime | None = None) -> None:
        """Refuse **avant** l'acte, avec un message qui dit quoi faire.

        Vérifier après aurait laissé l'appel coûteux se produire — et pour le
        copilote, l'appel coûteux *est* la dépense qu'on plafonne.

        La dépense est vérifiée en même temps que le nombre d'appels : un plan
        peut être dans ses appels et hors de son budget, parce qu'une question
        longue coûte davantage qu'une question courte.
        """
        snapshot = await self.snapshot(now=now)
        decisions = {
            UsageMetric.AGENT_MESSAGE: snapshot.agent_messages,
            UsageMetric.ANALYTICS_QUERY: snapshot.analytics_queries,
        }
        for decision in (decisions[metric], snapshot.llm_spend):
            if not decision.allowed:
                logger.warning(
                    "quota_exceeded",
                    tenant=str(self._context.tenant_id),
                    metric=metric.value,
                    label=decision.label_fr,
                    used=decision.used,
                    limit=decision.limit,
                )
                raise QuotaExceededError(
                    decision.message_fr or "Plafond atteint.",
                    remedy_fr=decision.remedy_fr,
                )

    async def check_stock(self, kind: str) -> None:
        """Plafond de stock, avant création.

        `kind` vaut `"field"` ou `"shipment"` — une chaîne plutôt qu'une
        énumération parce que ce sont des tables, pas des métriques comptées.
        """
        snapshot = await self.snapshot()
        decision = snapshot.fields if kind == "field" else snapshot.shipments
        if not decision.allowed:
            raise QuotaExceededError(
                decision.message_fr or "Plafond atteint.",
                remedy_fr=decision.remedy_fr,
            )

    async def record(
        self,
        metric: UsageMetric,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float | None = None,
        model: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """Enregistre la consommation **après** l'acte.

        Après, parce qu'un acte qui a échoué n'a pas été consommé — et facturer
        une panne est la façon la plus sûre de perdre la confiance qu'un compteur
        d'usage doit inspirer.
        """
        self._session.add(
            UsageEvent(
                id=uuid.uuid4(),
                tenant_id=self._context.tenant_id,
                metric=metric,
                quantity=1,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
                model=model,
                run_id=run_id,
                actor_user_id=self._context.user_id,
            )
        )
        await self._session.flush()

    # -- comptage ------------------------------------------------------------

    async def _count_rows(self, model: type[Field] | type[Shipment]) -> int:
        return int(
            (
                await self._session.execute(select(func.count()).select_from(model))
            ).scalar_one()
        )

    async def _count_events(self, metric: UsageMetric, start: datetime) -> int:
        return int(
            (
                await self._session.execute(
                    select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
                        UsageEvent.metric == metric,
                        UsageEvent.occurred_at >= start,
                    )
                )
            ).scalar_one()
        )

    async def _spend(self, start: datetime) -> tuple[float, bool]:
        """Dépense du mois, et si elle est partielle.

        Un appel non tarifé porte `cost_usd IS NULL`. L'additionner comme zéro
        présenterait un total incomplet comme complet ; on rend donc la somme des
        appels tarifés **et** le fait qu'elle soit minorante, pour que l'écran
        puisse le dire.
        """
        row = (
            await self._session.execute(
                select(
                    func.coalesce(func.sum(UsageEvent.cost_usd), 0.0),
                    func.count().filter(UsageEvent.cost_usd.is_(None)),
                ).where(UsageEvent.occurred_at >= start)
            )
        ).one()
        return float(row[0]), int(row[1]) > 0
