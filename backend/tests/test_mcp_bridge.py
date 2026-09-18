"""Le second consommateur du registre.

Ce que ce fichier vérifie n'est pas que MCP « fonctionne » : c'est que le pont
n'a **pas** sa propre définition d'outil. Deux définitions — une pour l'agent,
une pour MCP — divergent en silence, et l'écart se découvre chez un client
externe, c'est-à-dire au pire endroit.
"""

from __future__ import annotations

import json

from app.db.session import Databases
from app.mcp_server import McpBridge
from app.tools import business_tools
from app.tools.registry import registry
from tests.conftest import Org

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés sans marqueur.


def test_the_bridge_exposes_the_registry_and_nothing_else(
    databases: Databases, demo_org: Org
) -> None:
    """Aucune capacité n'est déclarée ici.

    Un outil ajouté au registre apparaît sur MCP sans qu'une ligne soit écrite ;
    un outil retiré disparaît. C'est la propriété qui rend la duplication
    impossible plutôt que déconseillée.
    """
    bridge = McpBridge(registry, databases, demo_org.context)
    names = {tool["name"] for tool in bridge.list_tools()}
    assert names == {d.name for d in registry.all()}
    assert names == set(business_tools.ALL_TOOL_NAMES)


def test_the_bridge_speaks_mcp_not_anthropic(
    databases: Databases, demo_org: Org
) -> None:
    """Même définition, deux dialectes : `inputSchema` ici, `input_schema` là.

    La traduction est le seul travail du pont, et elle doit rester sérialisable.
    """
    bridge = McpBridge(registry, databases, demo_org.context)
    for tool in bridge.list_tools():
        assert set(tool) == {"name", "description", "inputSchema"}
        assert tool["inputSchema"]["additionalProperties"] is False
        json.dumps(tool, ensure_ascii=False)


def test_no_mcp_tool_takes_a_tenant(databases: Databases, demo_org: Org) -> None:
    """Un client MCP ne peut pas désigner une autre organisation.

    La session MCP est ouverte **pour** une organisation, à partir d'un jeton, et
    il n'existe aucun paramètre par lequel en nommer une autre. La garantie
    traverse le protocole intacte parce qu'elle n'est pas dans le protocole.
    """
    bridge = McpBridge(registry, databases, demo_org.context)
    for tool in bridge.list_tools():
        assert "tenant_id" not in tool["inputSchema"].get("properties", {})


def test_an_analyst_sees_a_smaller_surface(
    databases: Databases, demo_org: Org
) -> None:
    """Le filtrage par rôle est celui du registre, pas un second contrôle.

    Un second contrôle serait un second endroit où se tromper.
    """
    from dataclasses import replace

    from app.domain.enums import UserRole

    analyst = replace(demo_org.context, role=UserRole.ANALYST)
    admin_tools = {t["name"] for t in McpBridge(registry, databases, demo_org.context).list_tools()}
    analyst_tools = {t["name"] for t in McpBridge(registry, databases, analyst).list_tools()}
    assert "create_recommendation" in admin_tools
    assert "create_recommendation" not in analyst_tools
    assert analyst_tools < admin_tools


async def test_a_call_returns_wrapped_content(
    databases: Databases, demo_org: Org
) -> None:
    """Un client MCP est un modèle : le résultat reste une donnée encapsulée.

    Même encapsulation que côté agent, et pour la même raison — un nom de site
    est du texte qu'un utilisateur a saisi.
    """
    bridge = McpBridge(registry, databases, demo_org.context)
    response = await bridge.call_tool("get_shipment", {"reference": "EXP-1842"})

    assert response["isError"] is False
    text = response["content"][0]["text"]
    assert text.startswith("<donnees_externes")
    payload = json.loads(text.split("\n", 1)[1].rsplit("</donnees_externes>", 1)[0])
    assert payload["reference"] == "EXP-1842"
    assert "Casablanca" in payload["destination_site_fr"]


async def test_a_failing_call_is_an_mcp_error_not_an_exception(
    databases: Databases, demo_org: Org
) -> None:
    """Le pont ne laisse pas remonter d'exception vers le client.

    `isError` porte l'échec, et la charge utile française porte la raison. Rien
    n'est substitué au résultat manquant.
    """
    bridge = McpBridge(registry, databases, demo_org.context)
    response = await bridge.call_tool("get_shipment", {"reference": "EXP-0000"})

    assert response["isError"] is True
    assert "introuvable" in response["content"][0]["text"]
    assert "volume_tonnes" not in response["content"][0]["text"]


async def test_a_call_is_scoped_to_the_session_tenant(
    databases: Databases, demo_org: Org, two_orgs: tuple[Org, Org]
) -> None:
    """La même référence, une autre organisation, aucun résultat.

    L'expédition existe : elle est simplement invisible depuis une session MCP
    ouverte ailleurs. C'est ce qu'un test à une seule organisation ne peut pas
    montrer.
    """
    alpha, _ = two_orgs
    mine = await McpBridge(registry, databases, demo_org.context).call_tool(
        "get_shipment", {"reference": "EXP-1842"}
    )
    theirs = await McpBridge(registry, databases, alpha.context).call_tool(
        "get_shipment", {"reference": "EXP-1842"}
    )
    assert mine["isError"] is False
    assert theirs["isError"] is True
    assert "introuvable" in theirs["content"][0]["text"]


def test_the_bridge_describes_itself_for_an_operator(
    databases: Databases, demo_org: Org
) -> None:
    """Un diagnostic d'exploitation nomme l'organisation et le rôle.

    Sans cela, « le serveur MCP ne voit rien » est indiscernable de « le serveur
    MCP regarde la mauvaise organisation ».
    """
    described = json.loads(McpBridge(registry, databases, demo_org.context).describe())
    assert described["tenant"] == str(demo_org.tenant_id)
    assert described["role"] == "ADMIN"
    assert sorted(described["tools"]) == sorted(business_tools.ALL_TOOL_NAMES)
