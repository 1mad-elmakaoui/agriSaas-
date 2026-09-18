"""Contrat d'un fournisseur de modèle.

Une abstraction fine, pour une seule raison : la boucle d'agent doit être
testable **sans clé d'API et sans réseau**. Un fournisseur scripté rejoue une
séquence de blocs `tool_use` et permet de vérifier la mécanique de la boucle —
l'injection du tenant, l'encapsulation des résultats, le message unique, la
borne d'itérations, la trace.

Ce que cela ne teste pas, et qu'il faut dire : le comportement du vrai modèle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

__all__ = ["LLMError", "LLMProvider", "LLMResponse", "ToolCall"]


class LLMError(RuntimeError):
    """Échec d'appel du modèle. Traduit en français à la frontière."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Réponse d'un tour de modèle, réduite à ce dont la boucle a besoin."""

    text: str
    tool_calls: tuple[ToolCall, ...]
    stop_reason: str
    model: str
    input_tokens: int
    output_tokens: int
    #: Blocs bruts, réinjectés tels quels dans l'historique. Les renvoyer
    #: inchangés est ce que l'API attend ; les reconstruire perdrait les blocs
    #: de réflexion et casserait la continuation.
    raw_content: Any = None
    refusal_category: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    @property
    def is_refusal(self) -> bool:
        return self.stop_reason == "refusal"


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> LLMResponse: ...
