"""Boucle d'agent — manuelle, bornée, tracée.

Manuelle plutôt que via l'exécuteur du SDK, pour deux raisons que la
spécification demande de conserver : une session de base de données **portée par
la requête** doit être injectée dans chaque outil, et la trace exacte des appels
doit atteindre l'interface pour qu'un exploitant puisse auditer quel appel a
produit quel chiffre.

Quatre détails coûteux à réapprendre, tous vérifiés par des tests :

1. **Tous les résultats d'outils repartent dans un seul message utilisateur.**
   Les répartir sur plusieurs messages désapprend au modèle les appels
   parallèles, et la latence s'en ressent immédiatement.
2. **Un outil qui échoue ne casse pas la conversation.** Le résultat devient une
   charge utile française avec `is_error`, et rien n'est substitué au résultat
   manquant.
3. **La borne d'itérations est une vraie condition d'arrêt**, avec une issue
   visible par l'utilisateur — pas une troncature silencieuse.
4. **Un refus** (`stop_reason == "refusal"`) produit un décline poli en
   français, jamais une relance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent.prompt import PROMPT_VERSION, build_system_prompt
from app.core.errors import FeatureDisabledError, ProviderUnavailableError
from app.core.logging import current_run_id, get_logger
from app.core.untrusted import sanitize_user_text
from app.llm.base import LLMError, LLMProvider
from app.observability.cost import TokenUsage, estimate_cost_usd
from app.tools.registry import ToolContext, ToolRegistry

logger = get_logger(__name__)

__all__ = ["AgentAnswer", "AgentService", "ToolInvocation"]


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """Un appel d'outil, tel que « Voir les calculs » l'affiche.

    Porte le **résultat structuré**, pas seulement le fait qu'un appel a eu
    lieu : c'est ce qui permet à l'interface de reconstruire la carte de décision
    depuis la trace plutôt que depuis un bloc rédigé par le modèle
    (décision 0005).
    """

    name: str
    arguments: dict[str, Any]
    succeeded: bool
    result: dict[str, Any]
    error_fr: str | None = None


@dataclass(slots=True)
class AgentAnswer:
    text_fr: str
    tool_calls: list[ToolInvocation] = field(default_factory=list)
    iterations: int = 0
    stop_reason: str = ""
    model: str = ""
    prompt_version: str = PROMPT_VERSION
    run_id: str | None = None
    usage: list[TokenUsage] = field(default_factory=list)
    truncated: bool = False

    @property
    def total_cost_usd(self) -> float | None:
        """`None` dès qu'un appel n'est pas tarifé — jamais un total partiel
        présenté comme complet."""
        if any(u.cost_usd is None for u in self.usage):
            return None
        return round(sum(u.cost_usd or 0.0 for u in self.usage), 6)


class AgentService:
    def __init__(
        self,
        *,
        provider: LLMProvider | None,
        registry: ToolRegistry,
        context: ToolContext,
        tenant_name: str,
        is_demo: bool,
        max_iterations: int = 8,
        max_tokens: int = 16_000,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._context = context
        self._tenant_name = tenant_name
        self._is_demo = is_demo
        self._max_iterations = max_iterations
        self._max_tokens = max_tokens

    async def ask(self, question: str) -> AgentAnswer:
        if self._provider is None:
            # Le copilote n'est jamais simulé : une réponse fabriquée localement
            # « pour faire fonctionner la démo » donnerait une fausse idée de ce
            # que le produit sait faire.
            raise FeatureDisabledError(
                "Le copilote n'est pas configuré sur cette installation : aucune "
                "clé d'API n'est renseignée.",
                remedy_fr=(
                    "Les recommandations d'irrigation et les analyses restent "
                    "accessibles dans le reste de l'application."
                ),
            )

        cleaned = sanitize_user_text(question)
        if not cleaned:
            raise FeatureDisabledError("La question est vide.")

        system = build_system_prompt(
            is_demo=self._is_demo, tenant_name=self._tenant_name
        )
        tools = self._registry.anthropic_specs(self._context.request)
        messages: list[dict[str, Any]] = [{"role": "user", "content": cleaned}]

        answer = AgentAnswer(text_fr="", run_id=current_run_id())
        iterations = 0

        while iterations < self._max_iterations:
            iterations += 1
            try:
                response = await self._provider.complete(
                    system=system,
                    messages=messages,
                    tools=tools,
                    max_tokens=self._max_tokens,
                )
            except LLMError as exc:
                logger.error("agent_provider_failed", error=str(exc))
                raise ProviderUnavailableError(
                    "Le service d'intelligence artificielle est momentanément "
                    "indisponible.",
                    remedy_fr=(
                        "Les analyses déterministes restent accessibles dans le "
                        "reste de l'application."
                    ),
                ) from exc

            answer.model = response.model
            answer.stop_reason = response.stop_reason
            answer.usage.append(
                TokenUsage(
                    model=response.model,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cost_usd=estimate_cost_usd(
                        response.model, response.input_tokens, response.output_tokens
                    ),
                )
            )

            if response.is_refusal:
                # Un refus se relaie, il ne se relance pas.
                answer.text_fr = (
                    "Je ne peux pas répondre à cette demande. Reformulez-la ou "
                    "adressez-vous à un responsable de votre organisation."
                )
                answer.iterations = iterations
                return answer

            if not response.wants_tools:
                answer.text_fr = response.text
                answer.iterations = iterations
                return answer

            messages.append(
                {
                    "role": "assistant",
                    "content": response.raw_content
                    if response.raw_content is not None
                    else [
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments,
                        }
                        for call in response.tool_calls
                    ],
                }
            )

            results: list[dict[str, Any]] = []
            for call in response.tool_calls:
                payload, failed = await self._registry.execute(
                    call.name, call.arguments, self._context
                )
                answer.tool_calls.append(
                    ToolInvocation(
                        name=call.name,
                        arguments=call.arguments,
                        succeeded=not failed,
                        result=payload,
                        error_fr=payload.get("erreur") if failed else None,
                    )
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        # Encapsulé par le registre : un nom de site ou une note
                        # de terrain est du texte qu'un utilisateur a saisi.
                        "content": self._registry.envelope_for(call.name, payload),
                        "is_error": failed,
                    }
                )

            # Un seul message, quel que soit le nombre d'outils appelés.
            messages.append({"role": "user", "content": results})

        # Borne atteinte : on le dit, plutôt que de présenter une réponse
        # partielle comme si elle était complète.
        logger.warning(
            "agent_iteration_budget_exhausted",
            iterations=iterations,
            tenant=str(self._context.request.tenant_id),
        )
        answer.iterations = iterations
        answer.truncated = True
        answer.stop_reason = "max_iterations"
        answer.text_fr = (
            "L'analyse n'a pas pu être menée à son terme dans le nombre d'étapes "
            "autorisé. Les résultats intermédiaires sont visibles dans le détail "
            "des calculs."
        )
        return answer
