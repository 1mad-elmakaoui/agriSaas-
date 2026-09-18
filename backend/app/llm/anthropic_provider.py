"""Fournisseur Anthropic réel.

**Jamais appelé dans cet environnement** : aucune clé n'y est disponible et le
proxy refuse les hôtes tiers. Le code est écrit, il n'est pas vérifié — c'est
une ligne du registre d'honnêteté, pas une formalité.

Quatre détails d'API qui ne se devinent pas :

* la réflexion adaptative est le mode courant ; `budget_tokens` est **rejeté**
  par Opus 5 et n'a pas à figurer ici ;
* l'effort se règle dans `output_config`, pas au niveau supérieur ;
* `stop_reason == "refusal"` arrive en HTTP 200 : il faut le lire **avant** le
  contenu, sinon on interprète une réponse vide ;
* les blocs de contenu se réinjectent tels quels dans l'historique ;
* `betas` et `fallbacks` n'existent que sur `client.beta.messages` — les
  passer à `client.messages` est une erreur de typage, pas un paramètre
  ignoré.
"""

from __future__ import annotations

from typing import Any, cast

import anthropic
from anthropic.types.beta import (
    BetaMessageParam,
    BetaOutputConfigParam,
    BetaThinkingConfigAdaptiveParam,
    BetaToolParam,
)

from app.core.logging import get_logger
from app.llm.base import LLMError, LLMResponse, ToolCall

logger = get_logger(__name__)

__all__ = ["AnthropicProvider"]

#: Repli côté serveur en cas de refus des classificateurs de sûreté. Sans lui,
#: une question légitime mal classée renvoie une réponse vide à un exploitant.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, model: str, *, effort: str = "high") -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self.model = model
        self._effort = effort

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        try:
            response = await self._client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                # Les paramètres du SDK sont des `TypedDict`. Le registre
                # produit des dictionnaires bien formés mais non typés à la
                # frontière ; le `cast` dit exactement cela, et n'ajoute aucune
                # conversion à l'exécution.
                tools=cast(list[BetaToolParam], tools),
                messages=cast(list[BetaMessageParam], messages),
                thinking=BetaThinkingConfigAdaptiveParam(type="adaptive"),
                output_config=cast(
                    BetaOutputConfigParam, {"effort": self._effort}
                ),
                betas=[_FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIError as exc:
            logger.error("anthropic_call_failed", error=str(exc))
            raise LLMError(str(exc)) from exc

        # Lu avant le contenu : un refus arrive en 200 avec un contenu vide.
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            return LLMResponse(
                text="",
                tool_calls=(),
                stop_reason="refusal",
                model=self.model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                raw_content=response.content,
                refusal_category=getattr(details, "category", None),
            )

        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        calls = tuple(
            ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {}))
            for block in response.content
            if block.type == "tool_use"
        )
        return LLMResponse(
            text=text,
            tool_calls=calls,
            stop_reason=str(response.stop_reason),
            model=self.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            raw_content=response.content,
        )
