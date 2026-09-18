"""Abonnement : le plan, les plafonds, et où en est la consommation.

Aucun paiement n'est traité ici (§6) : un plan est **provisionné** par un
administrateur, pas acheté. Ce que cette surface expose est le compteur qu'une
intégration de facturation consommerait plus tard.

Une jauge affiche toujours son dénominateur. « 87 » ne dit rien ; « 87 sur 100
questions » dit qu'il en reste treize, et c'est la seule forme sur laquelle
quelqu'un peut agir.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentAudit, CurrentContext, CurrentSession
from app.api.schemas import (
    PlanChangeIn,
    PlanOut,
    QuotaOut,
    SubscriptionOut,
)
from app.core.errors import AuthorizationError, ValidationError
from app.domain.enums import UserRole
from app.domain.quotas import PlanLimits, QuotaDecision
from app.services.quota_service import QuotaService

router = APIRouter(prefix="/abonnement", tags=["Abonnement"])


def _quota_out(decision: QuotaDecision) -> QuotaOut:
    return QuotaOut(
        label_fr=decision.label_fr,
        used=round(decision.used, 4),
        limit=decision.limit,
        remaining=decision.remaining,
        unit_fr=decision.unit_fr,
        fraction=decision.fraction,
        allowed=decision.allowed,
        message_fr=decision.message_fr,
        remedy_fr=decision.remedy_fr,
    )


def _plan_out(plan: PlanLimits) -> PlanOut:
    return PlanOut(
        code=plan.code,
        name_fr=plan.name_fr,
        description_fr=plan.description_fr,
        max_fields=plan.max_fields,
        max_shipments=plan.max_shipments,
        max_agent_messages_per_month=plan.max_agent_messages_per_month,
        max_analytics_queries_per_month=plan.max_analytics_queries_per_month,
        max_llm_spend_usd_per_month=plan.max_llm_spend_usd_per_month,
    )


@router.get("", response_model=SubscriptionOut, summary="Plan et consommation")
async def subscription(
    session: CurrentSession, context: CurrentContext
) -> SubscriptionOut:
    service = QuotaService(session, context)
    snapshot = await service.snapshot()
    available = await service.available_plans()

    return SubscriptionOut(
        plan=_plan_out(snapshot.plan),
        available_plans=[_plan_out(p) for p in available],
        period_start=snapshot.period_start,
        fields=_quota_out(snapshot.fields),
        shipments=_quota_out(snapshot.shipments),
        agent_messages=_quota_out(snapshot.agent_messages),
        analytics_queries=_quota_out(snapshot.analytics_queries),
        llm_spend=_quota_out(snapshot.llm_spend),
        spend_is_partial=snapshot.spend_is_partial,
        spend_notice_fr=(
            "Au moins un appel du mois n'est pas tarifé : la dépense affichée est "
            "un minorant. Elle est de toute façon dérivée d'une grille tarifaire, "
            "et n'a été rapprochée d'aucune facture."
            if snapshot.spend_is_partial
            else "Dépense dérivée d'une grille tarifaire publiée, non rapprochée "
            "d'une facture."
        ),
    )


@router.post("/plan", response_model=SubscriptionOut, summary="Changer de plan")
async def change_plan(
    payload: PlanChangeIn,
    session: CurrentSession,
    context: CurrentContext,
    audit: CurrentAudit,
) -> SubscriptionOut:
    """Provisionne un plan. Réservé à l'administrateur de l'organisation.

    Un changement de plan modifie ce que l'organisation a le droit de consommer.
    C'est exactement le genre d'acte dont un audit doit pouvoir nommer l'auteur,
    et le journal le consigne avec l'ancien et le nouveau plan.
    """
    if context.role is not UserRole.ADMIN:
        raise AuthorizationError(
            "Seul un administrateur peut changer le plan de l'organisation.",
            remedy_fr="Demandez à un administrateur de votre organisation.",
        )

    service = QuotaService(session, context)
    previous = await service.plan()
    codes = {plan.code for plan in await service.available_plans()}
    if payload.plan_code not in codes:
        raise ValidationError(
            f"Le plan « {payload.plan_code} » n'existe pas.",
            remedy_fr=f"Plans disponibles : {', '.join(sorted(codes))}.",
        )

    await service.set_plan(payload.plan_code)
    await audit.record(
        context,
        action="subscription:change_plan",
        resource_type="TENANT",
        resource_id=str(context.tenant_id),
        outcome="SUCCESS",
        detail={"from": previous.code, "to": payload.plan_code},
    )
    return await subscription(session, context)
