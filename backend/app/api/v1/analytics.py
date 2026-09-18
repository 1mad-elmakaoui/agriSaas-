"""Point d'entrée de l'analyse en langage naturel.

Il n'existe **aucune** route qui accepte du SQL. Celle-ci accepte une question ;
le SQL est produit, validé, planifié et exécuté à l'intérieur, par le seul chemin
d'exécution du produit. Ajouter un second chemin lui donnerait aucune des raisons
pour lesquelles le premier est sûr.

La portée analytique vient d'une dépendance qui exige un contexte authentifié :
il n'y a pas de manière d'appeler ce service sans organisation.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.analytics.service import AnalyticsService
from app.api.deps import (
    AgentRateLimit,
    CurrentAnalytics,
    CurrentAudit,
    CurrentContext,
    CurrentProvider,
    CurrentSession,
)
from app.api.schemas import AnalysisSchema, AttemptSchema
from app.domain.quotas import UsageMetric
from app.services.quota_service import QuotaService

router = APIRouter(prefix="/analyse", tags=["Analyse"],
    # Le seuil strict du §9 s'applique à toutes les routes de ce routeur,
    # y compris celles qui n'existent pas encore : une dépendance posée
    # ici ne s'oublie pas comme un décorateur route par route.
    dependencies=[AgentRateLimit],
)


class QuestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1_000)


@router.post("", response_model=AnalysisSchema, summary="Interroger les données")
async def analyse(
    payload: QuestionIn,
    context: CurrentContext,
    scope: CurrentAnalytics,
    provider: CurrentProvider,
    audit: CurrentAudit,
    session: CurrentSession,
) -> AnalysisSchema:
    quotas = QuotaService(session, context)
    await quotas.require(UsageMetric.ANALYTICS_QUERY)

    answer = await AnalyticsService(provider=provider, scope=scope).ask(payload.question)

    await quotas.record(
        UsageMetric.ANALYTICS_QUERY,
        input_tokens=sum(u.input_tokens for u in answer.usage),
        output_tokens=sum(u.output_tokens for u in answer.usage),
        cost_usd=answer.total_cost_usd,
        model=next((u.model for u in answer.usage), None),
        run_id=answer.run_id,
    )

    await audit.record(
        context,
        action="analytics:ask",
        resource_type="ANALYTICS",
        outcome="REFUSED" if answer.refused else "SUCCESS",
        detail={
            "attempts": len(answer.attempts),
            "statuses": [a.status for a in answer.attempts],
            "prompt_version": answer.prompt_version,
        },
    )

    result = answer.result
    cost = answer.total_cost_usd
    return AnalysisSchema(
        question=answer.question,
        text_fr=answer.text_fr,
        sql=answer.sql,
        columns=list(result.column_names) if result else [],
        rows=[dict(row) for row in result.rows] if result else [],
        row_count=result.row_count if result else 0,
        truncated=result.truncated if result else False,
        truncation_reason_fr=result.truncation_reason if result else None,
        attempts=[
            AttemptSchema(
                index=a.index,
                sql=a.sql,
                status=a.status,
                issues_fr=list(a.issues_fr),
                plan_cost=a.plan_cost,
                duration_ms=a.duration_ms,
            )
            for a in answer.attempts
        ],
        refused=answer.refused,
        refusal_reason_fr=answer.refusal_reason_fr,
        prompt_version=answer.prompt_version,
        run_id=answer.run_id,
        cost_usd=cost,
        cost_label_fr=(
            f"{cost:.4f} USD"
            if cost is not None
            else "Coût inconnu : au moins un appel n'est pas tarifé."
        ),
    )
