"""Fournisseur scripté, pour tester la boucle sans clé ni réseau.

Il ne simule **pas** un modèle : il rejoue une séquence décidée par le test.
C'est la distinction qui compte. Il prouve que la boucle appelle les bons
outils, encapsule les résultats, les renvoie en un seul message, respecte sa
borne et produit une trace. Il ne prouve rien sur le choix d'outil que ferait
Claude devant une vraie question — cela demande une clé, et le registre
d'honnêteté le dit.

Interdit en production par `Settings._production_guards`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.llm.base import LLMResponse, ToolCall

__all__ = ["FakeProvider", "scripted_text", "scripted_tool_call"]


def scripted_tool_call(name: str, **arguments: Any) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=(ToolCall(id=f"call_{name}", name=name, arguments=arguments),),
        stop_reason="tool_use",
        model="fake",
        input_tokens=0,
        output_tokens=0,
    )


def scripted_text(text: str, *, stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=(),
        stop_reason=stop_reason,
        model="fake",
        input_tokens=0,
        output_tokens=0,
    )


class FakeProvider:
    """Rejoue une liste de réponses, dans l'ordre.

    Conserve les messages reçus : c'est ce qui permet d'affirmer que les
    résultats d'outils sont bien revenus **dans un seul message utilisateur** et
    qu'ils étaient bien encapsulés.
    """

    name = "fake"
    model = "fake"

    def __init__(self, script: Sequence[LLMResponse]) -> None:
        self._script = list(script)
        self._index = 0
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.seen_systems: list[str] = []
        self.seen_tools: list[list[dict[str, Any]]] = []

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse:
        self.seen_messages.append([dict(m) for m in messages])
        self.seen_systems.append(system)
        self.seen_tools.append(list(tools))
        if self._index >= len(self._script):
            # Script épuisé : on termine proprement plutôt que de boucler.
            return scripted_text("(script épuisé)")
        response = self._script[self._index]
        self._index += 1
        return response

    @property
    def call_count(self) -> int:
        return self._index
