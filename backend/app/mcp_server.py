"""Serveur MCP — le **second** consommateur du même registre.

Il n'y a pas de définition d'outil ici, et c'est tout l'intérêt du fichier. Il
traduit le registre dans le protocole MCP et exécute par le même chemin, avec la
même validation, les mêmes rôles et le même journal d'audit. Un outil ajouté au
registre apparaît ici sans qu'une ligne ne soit écrite.

La contrainte d'isolation traverse le protocole intacte : une session MCP est
ouverte **pour une organisation**, à partir d'un jeton, et `tenant_id` n'est le
paramètre d'aucun outil. Un client MCP ne peut donc pas en désigner une autre.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.core.security import RequestContext
from app.db.session import Databases
from app.tools.registry import ToolContext, ToolRegistry

logger = get_logger(__name__)

__all__ = ["McpBridge"]


class McpBridge:
    """Expose le registre au protocole MCP.

    Séparé du transport stdio : la traduction est testable sans lancer de
    processus, et c'est elle qui porte la garantie d'isolation.
    """

    def __init__(
        self, registry: ToolRegistry, databases: Databases, context: RequestContext
    ) -> None:
        self._registry = registry
        self._databases = databases
        self._context = context

    def list_tools(self) -> list[dict[str, Any]]:
        """Outils visibles par cet appelant, au format MCP.

        Filtrés par rôle, comme du côté agent : les deux consommateurs voient la
        même surface parce qu'ils lisent le même registre.
        """
        return [
            {
                "name": spec["name"],
                "description": spec["description"],
                "inputSchema": spec["input_schema"],
            }
            for spec in self._registry.anthropic_specs(self._context)
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Exécute un outil et rend une réponse MCP.

        La session est ouverte **liée à l'organisation** de la session MCP :
        même par ce chemin, il n'existe aucun moyen d'en désigner une autre.
        """
        async with self._databases.for_tenant(self._context.tenant_id).begin() as session:
            payload, failed = await self._registry.execute(
                name, arguments, ToolContext(session=session, request=self._context)
            )

        return {
            "content": [
                {
                    "type": "text",
                    # Même encapsulation que côté agent : un client MCP est un
                    # modèle, et le contenu reste une donnée.
                    "text": self._registry.envelope_for(name, payload),
                }
            ],
            "isError": failed,
        }

    def describe(self) -> str:
        """Résumé lisible du serveur, pour un diagnostic d'exploitation."""
        return json.dumps(
            {
                "tools": [t["name"] for t in self.list_tools()],
                "tenant": str(self._context.tenant_id),
                "role": self._context.role.value,
            },
            ensure_ascii=False,
            indent=2,
        )
