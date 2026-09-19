"""Ce que l'exploitation doit pouvoir tenir : journaux, corrélation, comptes.

La phase 8 a trouvé ses défauts en démarrant la pile, pas en la relisant. Ces
tests fixent ce qui a été corrigé, pour que la prochaine relecture ne suffise
pas non plus à le défaire.
"""

from __future__ import annotations

import io
import json
import logging
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.core.logging import configure_logging, get_logger, new_run_id, run_id_var
from app.db.session import Databases
from tests.conftest import Org

# `asyncio_mode = "auto"` : les tests asynchrones sont détectés sans marqueur.


@pytest.fixture
def captured_logs() -> io.StringIO:
    """Capture la sortie de journalisation, telle qu'un collecteur la recevrait."""
    stream = io.StringIO()
    configure_logging(json_output=True)
    root = logging.getLogger()
    for handler in root.handlers:
        handler.stream = stream  # type: ignore[attr-defined]
    yield stream
    # La suite parle à une console : on la lui rend.
    configure_logging(json_output=False)


# ---------------------------------------------------------------------------
# Un seul format sur un seul flux
# ---------------------------------------------------------------------------
def test_uvicorn_and_alembic_come_out_in_the_same_format(
    captured_logs: io.StringIO,
) -> None:
    """Deux formats sur le même flux font rejeter la moitié des lignes.

    Et c'est la moitié qui dit quelle requête a été servie.
    """
    get_logger("app").info("evenement_applicatif", champ=1)
    logging.getLogger("uvicorn.error").warning("Application startup failed.")
    logging.getLogger("alembic.runtime.migration").info("Running upgrade")

    lines = [line for line in captured_logs.getvalue().splitlines() if line.strip()]
    assert len(lines) == 3
    for line in lines:
        payload = json.loads(line)  # échoue si une ligne n'est pas du JSON
        assert "event" in payload
        assert "timestamp" in payload
        assert "level" in payload


def test_the_uvicorn_access_log_is_silenced_rather_than_reformatted() -> None:
    """L'application écrit sa propre ligne d'accès, avec durée et corrélation.

    Reformater celle d'uvicorn produirait deux lignes par requête, dont une sans
    ce qui sert à enquêter.
    """
    configure_logging(json_output=True)
    access = logging.getLogger("uvicorn.access")
    assert access.propagate is False
    assert access.handlers == []
    configure_logging(json_output=False)


def test_a_log_line_carries_the_run_id_of_its_request(
    captured_logs: io.StringIO,
) -> None:
    """Le `run_id` relie une ligne d'audit à la requête qui l'a produite."""
    token = run_id_var.set(new_run_id())
    try:
        get_logger("app").info("evenement")
        payload = json.loads(captured_logs.getvalue().splitlines()[-1])
        assert payload["run_id"] == run_id_var.get()
    finally:
        run_id_var.reset(token)


# ---------------------------------------------------------------------------
# La ligne d'accès
# ---------------------------------------------------------------------------
async def test_every_response_carries_its_correlation_id(
    client: AsyncClient,
) -> None:
    """Rendu en en-tête : un utilisateur qui signale un problème peut le citer."""
    response = await client.get("/api/sante")
    assert response.headers["X-Run-Id"]
    assert len(response.headers["X-Run-Id"]) == 32


async def test_a_refused_request_is_logged_like_any_other(
    client: AsyncClient, captured_logs: io.StringIO
) -> None:
    """Un 401 non journalisé rend une attaque par tâtonnement invisible."""
    await client.get("/api/v1/parcelles")
    access = [
        json.loads(line)
        for line in captured_logs.getvalue().splitlines()
        if line.strip() and json.loads(line).get("event") == "http_request"
    ]
    assert access, "la requête refusée doit laisser une ligne"
    assert access[-1]["status"] == 401
    assert access[-1]["path"] == "/api/v1/parcelles"
    assert access[-1]["duration_ms"] >= 0
    assert access[-1]["run_id"]


async def test_no_log_line_carries_a_query_string(
    client: AsyncClient, captured_logs: io.StringIO
) -> None:
    """Une chaîne de requête contient des identifiants, et rien ne garantit
    qu'elle ne contiendra jamais autre chose.

    Le test porte sur **tout le flux**, pas seulement sur notre ligne d'accès :
    c'est ainsi qu'on a découvert que `httpx` journalisait l'URL complète de
    chaque requête sortante — et avec elle tout ce qu'un fournisseur accepte
    dans une URL, une clé d'API y compris.
    """
    await client.get("/api/v1/parcelles?jeton=secret-a-ne-pas-journaliser")
    assert "secret-a-ne-pas-journaliser" not in captured_logs.getvalue()


# ---------------------------------------------------------------------------
# Les comptes d'exploitation
# ---------------------------------------------------------------------------
async def test_creating_a_user_twice_is_not_an_error(
    databases: Databases, demo_org: Org
) -> None:
    """`docker compose up` relancé ne doit pas échouer sur le second passage."""
    from app.domain.enums import UserRole
    from app.services.auth_service import AuthService

    email = f"ops-{uuid.uuid4().hex[:8]}@example.ma"
    service = AuthService(
        databases, jwt_secret="test-secret-not-a-real-one", ttl_minutes=60
    )
    await service.provision_user(
        tenant_id=demo_org.tenant_id,
        email=email,
        full_name="Compte d'exploitation",
        password="un-mot-de-passe-assez-long",
        role=UserRole.ANALYST,
    )

    async with databases.unscoped_session() as session:
        found = (
            await session.execute(
                text("SELECT count(*) FROM app.user_directory WHERE email = :e"),
                {"e": email},
            )
        ).scalar_one()
    # La commande `create-user` consulte cet annuaire avant d'écrire : c'est ce
    # qui la rend rejouable. Le test fixe l'invariant sur lequel elle s'appuie.
    assert found == 1


def test_the_cli_offers_every_role_the_domain_declares() -> None:
    """Une liste de rôles recopiée serait fausse au prochain rôle ajouté.

    Et l'erreur serait un choix impossible à taper dans une commande
    d'exploitation, découvert un soir de mise en production.
    """
    from app.cli import _roles
    from app.domain.enums import UserRole

    assert set(_roles()) == set(UserRole)
