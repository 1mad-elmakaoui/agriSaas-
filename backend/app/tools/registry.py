"""Le registre des capacités métier.

**Une définition par capacité. Deux consommateurs. Aucune exception.**

```
app/tools/registry.py          ← le seul endroit où une capacité est définie
        ├── app/mcp_server.py     l'expose sur stdio (Claude Desktop, Claude Code)
        └── app/agent/service.py  la consomme en process via l'API Anthropic
```

Définir chaque outil deux fois — une pour MCP, une pour la boucle de l'agent —
produit deux définitions qui divergent en silence, et l'écart se découvre en
production. Ici, ajouter un outil le rend immédiatement disponible aux deux, avec
la même validation et le même contrôle d'accès.

Quatre garanties portées par ce module, et non par le prompt :

1. **Le tenant n'est jamais un paramètre.** Il vient du contexte authentifié. Le
   modèle ne peut pas franchir une frontière d'organisation même si on le lui
   demande explicitement, parce qu'il n'existe aucun argument par lequel exprimer
   la demande.
2. **Les entrées sont validées avant exécution.** Un schéma Pydantic par outil.
3. **Les sorties sont des structures typées**, avec leur provenance. Un outil qui
   renvoie une phrase a déjà perdu le chiffre.
4. **Un outil qui échoue ne casse jamais la conversation.** L'erreur devient une
   charge utile française que le modèle peut relayer, et rien n'est substitué au
   résultat manquant.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AtlasError, AuthorizationError
from app.core.logging import get_logger
from app.core.security import RequestContext
from app.core.untrusted import wrap_untrusted
from app.domain.enums import UserRole
from app.repositories.tenant import TenantRepository

logger = get_logger(__name__)

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)

__all__ = [
    "ToolContext",
    "ToolDefinition",
    "ToolError",
    "ToolRegistry",
    "registry",
    "tool",
]


class ToolOutput(BaseModel):
    """Base des sorties d'outil.

    Exister comme type est le point : un outil ne peut pas rendre une chaîne
    formatée, donc il ne peut pas perdre le chiffre en route. Le panneau
    « Sources et preuves » lit ces champs sans que le modèle ait à retaper quoi
    que ce soit.
    """


@dataclass(slots=True)
class ToolContext:
    """Contexte d'exécution d'un outil.

    Porte l'identité de l'appelant et l'accès aux données. La session est déjà
    liée à l'organisation : un outil ne peut pas en sortir sans écrire
    délibérément une requête hors du dépôt — et la politique RLS le rattraperait.
    """

    session: AsyncSession
    request: RequestContext

    @property
    def repo(self) -> TenantRepository:
        return TenantRepository(self.session, self.request)


class ToolError(AtlasError):
    """Échec métier d'un outil, destiné à être relayé par le modèle."""

    code = "tool_failed"


@dataclass(frozen=True)
class ToolDefinition(Generic[InputT, OutputT]):
    """Définition complète d'une capacité métier."""

    name: str
    description_fr: str
    input_model: type[InputT]
    output_model: type[OutputT]
    handler: Callable[[InputT, ToolContext], Awaitable[OutputT]]
    allowed_roles: tuple[UserRole, ...] = ()
    #: Un outil « lecture seule » ne modifie rien. Les rares outils d'écriture
    #: sont marqués, pour pouvoir les restreindre depuis un seul endroit — et
    #: aucun d'eux n'exécute d'action irréversible : le plus engageant crée une
    #: proposition en attente d'approbation humaine.
    read_only: bool = True
    decision_support_fr: str = field(default="")

    def json_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = self.input_model.model_json_schema()
        schema.pop("title", None)
        return schema

    def anthropic_spec(self) -> dict[str, Any]:
        """Déclaration au format attendu par l'API Anthropic."""
        description = self.description_fr
        if self.decision_support_fr:
            description = f"{description}\n\nDécision soutenue : {self.decision_support_fr}"
        return {
            "name": self.name,
            "description": description,
            "input_schema": self.json_schema(),
        }

    def authorize(self, request: RequestContext) -> None:
        if self.allowed_roles:
            request.require_role(*self.allowed_roles)


class ToolRegistry:
    """Collection d'outils, avec exécution validée, encapsulée et journalisée."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition[Any, Any]] = {}

    def register(self, definition: ToolDefinition[Any, Any]) -> ToolDefinition[Any, Any]:
        if definition.name in self._tools:
            raise RuntimeError(
                f"Outil déjà enregistré : '{definition.name}'. Deux outils homonymes "
                "rendraient le comportement imprévisible, et l'un des deux serait "
                "silencieusement inatteignable."
            )
        self._tools[definition.name] = definition
        return definition

    def get(self, name: str) -> ToolDefinition[Any, Any]:
        if name not in self._tools:
            raise ToolError(
                f"Outil inconnu : « {name} ».",
                remedy_fr=f"Outils disponibles : {', '.join(sorted(self._tools))}.",
            )
        return self._tools[name]

    def all(self) -> list[ToolDefinition[Any, Any]]:
        return sorted(self._tools.values(), key=lambda t: t.name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def anthropic_specs(self, request: RequestContext) -> list[dict[str, Any]]:
        """Outils réellement utilisables par cet appelant.

        Filtrés ici plutôt qu'au moment de l'appel : cela évite au modèle de
        tenter un outil qu'il n'a pas le droit d'utiliser, puis d'avoir à
        interpréter un refus — et cela ne lui apprend pas l'existence de
        capacités que son rôle ne couvre pas.
        """
        usable: list[dict[str, Any]] = []
        for definition in self.all():
            try:
                definition.authorize(request)
            except AuthorizationError:
                continue
            usable.append(definition.anthropic_spec())
        return usable

    async def execute(
        self, name: str, raw_input: dict[str, Any], context: ToolContext
    ) -> tuple[dict[str, Any], bool]:
        """Exécute un outil. Renvoie `(charge utile, est_une_erreur)`.

        Renvoie **toujours** une structure exploitable. Une erreur métier devient
        un résultat `{"erreur": ...}` en français plutôt qu'une exception : le
        modèle doit pouvoir expliquer l'échec à l'utilisateur, pas s'interrompre
        au milieu d'une conversation.
        """
        started = time.monotonic()
        try:
            definition = self.get(name)
        except ToolError as exc:
            return {"erreur": exc.message_fr, "code": exc.code}, True

        try:
            definition.authorize(context.request)
        except AuthorizationError as exc:
            logger.info("tool_denied", tool=name, tenant=str(context.request.tenant_id))
            return {"erreur": exc.message_fr, "code": exc.code}, True

        try:
            payload = definition.input_model.model_validate(raw_input)
        except PydanticValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])} : {e['msg']}"
                for e in exc.errors()[:5]
            )
            return (
                {
                    "erreur": f"Paramètres invalides pour « {name} » : {details}",
                    "code": "donnees_invalides",
                },
                True,
            )

        try:
            result = await definition.handler(payload, context)
        except AtlasError as exc:
            logger.info("tool_business_error", tool=name, code=exc.code)
            return {"erreur": exc.message_fr, "code": exc.code}, True
        except Exception:
            logger.exception(
                "tool_unexpected_failure",
                tool=name,
                tenant=str(context.request.tenant_id),
            )
            return (
                {
                    "erreur": (
                        f"L'outil « {name} » n'a pas pu aboutir. L'incident a été "
                        "enregistré."
                    ),
                    "code": "erreur_interne",
                },
                True,
            )

        elapsed_ms = round((time.monotonic() - started) * 1000, 1)
        logger.info(
            "tool_executed",
            tool=name,
            tenant=str(context.request.tenant_id),
            duration_ms=elapsed_ms,
        )
        return result.model_dump(mode="json"), False

    def envelope_for(self, name: str, payload: dict[str, Any]) -> str:
        """Encapsule un résultat d'outil avant de le rendre au modèle.

        Appliqué **par le registre**, pas par chaque outil : le câblage ne dépend
        donc plus de la vigilance de celui qui en ajoute un. Un résultat contient
        des noms de sites, des références d'expédition et des notes de terrain —
        toutes des chaînes qu'un utilisateur a saisies un jour.
        """
        import json

        return wrap_untrusted(
            json.dumps(payload, ensure_ascii=False, default=str),
            origin_fr=f"résultat de l'outil {name}",
        )


registry = ToolRegistry()


def tool(
    *,
    name: str,
    description_fr: str,
    input_model: type[InputT],
    output_model: type[OutputT],
    allowed_roles: tuple[UserRole, ...] = (),
    read_only: bool = True,
    decision_support_fr: str = "",
) -> Callable[
    [Callable[[InputT, ToolContext], Awaitable[OutputT]]],
    Callable[[InputT, ToolContext], Awaitable[OutputT]],
]:
    """Décorateur d'enregistrement.

    Le schéma de **sortie** est obligatoire. `atlasagri` n'en avait pas — ses
    handlers rendaient `dict[str, Any]` — et c'est ce qui laissait une capacité
    renvoyer une phrase toute faite plutôt qu'une structure.
    """

    def decorator(
        handler: Callable[[InputT, ToolContext], Awaitable[OutputT]],
    ) -> Callable[[InputT, ToolContext], Awaitable[OutputT]]:
        registry.register(
            ToolDefinition(
                name=name,
                description_fr=description_fr,
                input_model=input_model,
                output_model=output_model,
                handler=handler,
                allowed_roles=allowed_roles,
                read_only=read_only,
                decision_support_fr=decision_support_fr,
            )
        )
        return handler

    return decorator
