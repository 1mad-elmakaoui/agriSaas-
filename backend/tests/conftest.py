"""Fixtures de test.

Le socle est testé contre un **vrai PostgreSQL**, jamais contre SQLite ni contre
un double. Ce qui est vérifié ici — politiques RLS, `FORCE`, propriété des
vues, privilèges de rôle — n'existe simplement pas ailleurs que dans PostgreSQL.
Un test d'isolation qui passerait sur SQLite ne prouverait rien du tout.

Sans DSN configuré, ces tests **échouent** au lieu d'être sautés. C'est le motif
de `text_to_sql` : un test d'isolation silencieusement sauté est indiscernable
d'un test d'isolation qui passe, et c'est précisément la confusion que ce
produit ne peut pas se permettre.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import Settings
from app.core.security import RequestContext
from app.db.session import Databases
from app.domain.enums import UserRole
from app.main import create_app
from app.services.auth_service import AuthService

APP_DSN_ENV = "ATLAS_TEST_DATABASE_URL"
ANALYTICS_DSN_ENV = "ATLAS_TEST_ANALYTICS_DATABASE_URL"
OWNER_DSN_ENV = "ATLAS_TEST_OWNER_DATABASE_URL"


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(
            f"{name} is not set. The isolation suite runs against a real "
            "PostgreSQL with the migration applied; skipping it would be "
            "indistinguishable from passing it."
        )
    return value


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url=_require(APP_DSN_ENV),
        analytics_database_url=_require(ANALYTICS_DSN_ENV),
        jwt_secret="test-secret-not-a-real-one",
        require_postgis=False,
    )


@pytest_asyncio.fixture
async def databases(settings: Settings) -> AsyncIterator[Databases]:
    db = Databases(settings)
    try:
        yield db
    finally:
        await db.dispose()


@dataclass(frozen=True)
class Org:
    """Une organisation de test, avec son utilisateur et son contexte."""

    tenant_id: uuid.UUID
    name: str
    email: str
    password: str
    context: RequestContext


@pytest_asyncio.fixture
async def two_orgs(databases: Databases) -> AsyncIterator[tuple[Org, Org]]:
    """Deux organisations réelles, créées puis supprimées.

    Deux, pas une : une suite d'isolation avec un seul tenant ne peut prouver
    que l'absence de données, jamais la présence d'une frontière.
    """
    owner_dsn = _require(OWNER_DSN_ENV)
    from sqlalchemy.ext.asyncio import create_async_engine

    owner = create_async_engine(owner_dsn, poolclass=None)
    created: list[uuid.UUID] = []
    orgs: list[Org] = []

    try:
        for label in ("alpha", "beta"):
            tenant_id = uuid.uuid4()
            created.append(tenant_id)
            async with owner.begin() as conn:
                await conn.execute(
                    text("SELECT set_config('app.current_tenant', :t, true)"),
                    {"t": str(tenant_id)},
                )
                await conn.execute(
                    text(
                        "INSERT INTO app.tenants (id, name, slug) "
                        "VALUES (:id, :name, :slug)"
                    ),
                    {
                        "id": tenant_id,
                        "name": f"Org {label}",
                        "slug": f"test-{label}-{tenant_id.hex[:8]}",
                    },
                )

            service = AuthService(databases, jwt_secret="test-secret-not-a-real-one",
                                  ttl_minutes=60)
            email = f"{label}-{tenant_id.hex[:8]}@example.ma"
            password = "correct-horse-battery-staple"
            user_id = await service.provision_user(
                tenant_id=tenant_id,
                email=email,
                full_name=f"Utilisateur {label}",
                password=password,
                role=UserRole.ADMIN,
            )
            orgs.append(
                Org(
                    tenant_id=tenant_id,
                    name=f"Org {label}",
                    email=email,
                    password=password,
                    context=RequestContext(
                        user_id=user_id,
                        tenant_id=tenant_id,
                        role=UserRole.ADMIN,
                        email=email,
                    ),
                )
            )
        yield orgs[0], orgs[1]
    finally:
        for tenant_id in created:
            async with owner.begin() as conn:
                await conn.execute(
                    text("SELECT set_config('app.current_tenant', :t, true)"),
                    {"t": str(tenant_id)},
                )
                await conn.execute(
                    text("DELETE FROM app.user_directory WHERE tenant_id = :t"),
                    {"t": tenant_id},
                )
                await conn.execute(
                    text("DELETE FROM app.tenants WHERE id = :t"), {"t": tenant_id}
                )
        await owner.dispose()


@pytest_asyncio.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    """L'application réelle, cycle de vie compris.

    Le cycle de vie est exécuté, donc les sondes de démarrage tournent : la
    suite échoue si le socle d'isolation n'est pas en place, avant même le
    premier test.

    Rendue séparément du client parce qu'un test qui exerce la boucle d'agent
    doit pouvoir substituer une dépendance — le fournisseur de modèle — sans
    reconstruire l'application ni court-circuiter le routeur.
    """
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        yield http_client


@pytest_asyncio.fixture
async def demo_org(databases: Databases) -> AsyncIterator[Org]:
    """Une organisation de démonstration complète, avec un compte pour s'y connecter.

    C'est le décor des deux questions que la phase doit savoir traiter : elles
    portent sur la parcelle P03 et sur l'expédition de tomates vers Casablanca,
    qui n'existent que dans le jeu de démonstration.
    """
    from app.db.seed import seed_demo_tenant

    owner_dsn = _require(OWNER_DSN_ENV)
    from sqlalchemy.ext.asyncio import create_async_engine

    owner = create_async_engine(owner_dsn)
    tenant_id = uuid.uuid4()
    try:
        async with owner.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": str(tenant_id)},
            )
            await conn.execute(
                text(
                    "INSERT INTO app.tenants (id, name, slug, region_code, is_demo) "
                    "VALUES (:id, :name, :slug, 'SOUSS_MASSA', true)"
                ),
                {
                    "id": tenant_id,
                    "name": "Souss Primeurs (démonstration)",
                    "slug": f"demo-agent-{tenant_id.hex[:8]}",
                },
            )
        async with databases.for_tenant(tenant_id).begin() as session:
            await seed_demo_tenant(session, tenant_id)

        service = AuthService(
            databases, jwt_secret="test-secret-not-a-real-one", ttl_minutes=60
        )
        email = f"demo-{tenant_id.hex[:8]}@example.ma"
        password = "correct-horse-battery-staple"
        user_id = await service.provision_user(
            tenant_id=tenant_id,
            email=email,
            full_name="Exploitant de démonstration",
            password=password,
            role=UserRole.ADMIN,
        )
        yield Org(
            tenant_id=tenant_id,
            name="Souss Primeurs (démonstration)",
            email=email,
            password=password,
            context=RequestContext(
                user_id=user_id,
                tenant_id=tenant_id,
                role=UserRole.ADMIN,
                email=email,
            ),
        )
    finally:
        async with owner.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": str(tenant_id)},
            )
            await conn.execute(
                text("DELETE FROM app.user_directory WHERE tenant_id = :t"),
                {"t": tenant_id},
            )
            await conn.execute(
                text("DELETE FROM app.tenants WHERE id = :t"), {"t": tenant_id}
            )
        await owner.dispose()
