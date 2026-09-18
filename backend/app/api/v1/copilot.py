"""Point d'entrée du copilote.

La réponse n'est **jamais** une prose seule : elle porte la trace d'outils, et
c'est depuis cette trace que l'interface reconstruit la carte de décision — pas
depuis un bloc JSON rédigé par le modèle (décision 0005).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.agent.prompt import PROMPT_VERSION
from app.agent.service import AgentService
from app.api.deps import (
    AgentRateLimit,
    CurrentAudit,
    CurrentContext,
    CurrentProvider,
    CurrentRepository,
    CurrentSession,
    CurrentSettings,
)
from app.domain.quotas import UsageMetric
from app.services.quota_service import QuotaService
from app.tools import business_tools  # noqa: F401 - enregistre les outils
from app.tools.registry import ToolContext, registry

router = APIRouter(prefix="/copilote", tags=["Copilote"],
    # Le seuil strict du §9 s'applique à toutes les routes de ce routeur,
    # y compris celles qui n'existent pas encore : une dépendance posée
    # ici ne s'oublie pas comme un décorateur route par route.
    dependencies=[AgentRateLimit],
)


class QuestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)


class ToolCallOut(BaseModel):
    name: str
    arguments: dict[str, Any]
    succeeded: bool
    result: dict[str, Any]
    error_fr: str | None


class CopilotOut(BaseModel):
    text_fr: str
    tool_calls: list[ToolCallOut]
    iterations: int
    truncated: bool
    stop_reason: str
    model: str
    prompt_version: str
    run_id: str | None
    #: `null` dès qu'un appel n'est pas tarifé : jamais un total partiel
    #: présenté comme complet.
    cost_usd: float | None
    cost_label_fr: str


@router.post("", response_model=CopilotOut, summary="Poser une question au copilote")
async def ask(
    payload: QuestionIn,
    context: CurrentContext,
    session: CurrentSession,
    settings: CurrentSettings,
    audit: CurrentAudit,
    provider: CurrentProvider,
    repository: CurrentRepository,
) -> CopilotOut:
    tenant = await repository.current_tenant()

    service = AgentService(
        provider=provider,
        registry=registry,
        context=ToolContext(session=session, request=context),
        tenant_name=tenant.name,
        is_demo=tenant.is_demo,
        max_iterations=settings.agent_max_tool_iterations,
    )
    # Le plafond est vérifié **avant** l'appel : pour le copilote, l'appel
    # coûteux est précisément la dépense qu'on plafonne, et vérifier après
    # l'aurait laissé se produire.
    quotas = QuotaService(session, context)
    await quotas.require(UsageMetric.AGENT_MESSAGE)

    answer = await service.ask(payload.question)

    # Enregistré **après** : un acte qui a échoué n'a pas été consommé, et
    # facturer une panne est la façon la plus sûre de perdre la confiance qu'un
    # compteur d'usage doit inspirer.
    await quotas.record(
        UsageMetric.AGENT_MESSAGE,
        input_tokens=sum(u.input_tokens for u in answer.usage),
        output_tokens=sum(u.output_tokens for u in answer.usage),
        cost_usd=answer.total_cost_usd,
        model=answer.model or None,
        run_id=answer.run_id,
    )

    await audit.record(
        context,
        action="copilot:ask",
        resource_type="COPILOT",
        outcome="SUCCESS" if not answer.truncated else "TRUNCATED",
        detail={
            "tools": [c.name for c in answer.tool_calls],
            "iterations": answer.iterations,
            "prompt_version": answer.prompt_version,
        },
    )

    cost = answer.total_cost_usd
    return CopilotOut(
        text_fr=answer.text_fr,
        tool_calls=[
            ToolCallOut(
                name=c.name,
                arguments=c.arguments,
                succeeded=c.succeeded,
                result=c.result,
                error_fr=c.error_fr,
            )
            for c in answer.tool_calls
        ],
        iterations=answer.iterations,
        truncated=answer.truncated,
        stop_reason=answer.stop_reason,
        model=answer.model,
        prompt_version=PROMPT_VERSION,
        run_id=answer.run_id,
        cost_usd=cost,
        cost_label_fr=(
            f"{cost:.4f} USD"
            if cost is not None
            else "Coût inconnu : au moins un appel n'est pas tarifé."
        ),
    )
