"""Moteurs et sessions — le tenant est lié à l'obtention d'une connexion.

C'est la pièce centrale de la décision 0001. La spécification demandait un
`SET LOCAL app.current_tenant` « dans la même transaction en lecture seule que
la requête ». Posé ainsi, c'est un appel qu'un chemin de code peut oublier — et
il y a plus de chemins qu'il n'y paraît : l'exécuteur analytique ouvre **deux**
transactions distinctes, une pour `EXPLAIN` et une pour la requête. Vérifié :
sans le GUC, `EXPLAIN` échoue au moment de la planification.

Donc le tenant n'est pas un paramètre d'appel. Il est un paramètre de
**construction**, et il n'existe aucune fonction publique ici qui rende une
session ou une connexion sans en avoir reçu un. `mypy --strict` fait respecter
la règle : on ne peut pas oublier un argument obligatoire.

Deux moteurs, jamais un :

* `app` — rôle applicatif, lecture-écriture sur le schéma `app` ;
* `analytics` — rôle SELECT-seul, privilèges sur les **vues** de `analytics` et
  aucun privilège sur `app`. C'est ce qui fait tenir la couche 2 même quand le
  validateur AST est le bug.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["AnalyticsScope", "Databases", "TenantSession"]

#: Nom du paramètre de session portant l'organisation courante. Les politiques
#: RLS le lisent **sans** `missing_ok` : un GUC absent doit produire une erreur
#: bruyante, jamais un jeu de lignes vide. Un jeu vide serait raconté par
#: l'agent d'analyse comme « vous n'avez aucune expédition ce mois-ci » — un
#: chiffre faux énoncé avec assurance, c'est-à-dire exactement ce que ce
#: produit existe pour éviter.
TENANT_GUC = "app.current_tenant"


def _normalise_dsn(dsn: str) -> str:
    """Force le pilote asyncpg.

    Une URL `postgresql://` nue sélectionne le dialecte psycopg2 synchrone, qui
    échoue en contexte async avec une erreur ne pointant nulle part près du DSN.
    """
    if dsn.startswith("postgresql+"):
        return dsn
    for prefix in ("postgresql://", "postgres://"):
        if dsn.startswith(prefix):
            return "postgresql+asyncpg://" + dsn[len(prefix) :]
    return dsn


async def _bind_tenant(conn: AsyncConnection, tenant_id: UUID) -> None:
    """Pose l'organisation courante pour la transaction en cours.

    `SET LOCAL` et non `SET` : la portée est la transaction, donc le partage de
    pool de connexions est sûr. Vérifié — la valeur ne survit pas au `COMMIT`.

    Le paramètre est lié, jamais interpolé. Le tenant vient d'un jeton signé,
    mais un identifiant de confiance interpolé dans du SQL reste une habitude
    qui finit par s'appliquer à un identifiant qui ne l'est pas.
    """
    await conn.execute(
        text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
        {"tenant": str(tenant_id)},
    )


@dataclass(frozen=True, slots=True)
class TenantSession:
    """Fabrique de sessions applicatives bornées à une organisation.

    Se construit depuis un `RequestContext`, jamais depuis une entrée
    utilisateur ni depuis un paramètre d'outil.
    """

    _sessionmaker: async_sessionmaker[AsyncSession]
    tenant_id: UUID

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[AsyncSession]:
        """Session transactionnelle avec l'organisation déjà posée.

        Le `SET LOCAL` est émis **avant** que quoi que ce soit d'autre ne touche
        la connexion : une requête qui s'exécuterait avant lui échouerait au
        cast du GUC absent — bruyamment, ce qui est le comportement voulu, mais
        inutilement.
        """
        async with self._sessionmaker() as session, session.begin():
            await _bind_tenant(await session.connection(), self.tenant_id)
            yield session


@dataclass(frozen=True, slots=True)
class AnalyticsScope:
    """Accès en lecture seule à `analytics`, borné à une organisation.

    Toute connexion rendue ici est déjà : en lecture seule, bornée dans le
    temps, et liée à l'organisation. Il n'y a pas de méthode qui en rende une
    sans l'être — c'est ce qui fait qu'un `ReadOnlyExecutor` ne peut pas être
    construit sans tenant.
    """

    _engine: AsyncEngine
    tenant_id: UUID
    statement_timeout_ms: int = 30_000
    idle_in_transaction_timeout_ms: int = 60_000
    lock_timeout_ms: int = 5_000

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[AsyncConnection]:
        async with self._engine.connect() as conn:
            transaction = await conn.begin()
            try:
                # `SET TRANSACTION READ ONLY` doit être la première instruction
                # de la transaction : elle précède donc les délais.
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                await conn.execute(
                    text(f"SET LOCAL statement_timeout = {int(self.statement_timeout_ms)}")
                )
                await conn.execute(
                    text(
                        "SET LOCAL idle_in_transaction_session_timeout = "
                        f"{int(self.idle_in_transaction_timeout_ms)}"
                    )
                )
                # Une lecture qui attend derrière un DDL sur la table cible
                # brûlerait sinon tout le délai d'instruction sur un verrou dont
                # elle n'a pas besoin.
                await conn.execute(text(f"SET LOCAL lock_timeout = {int(self.lock_timeout_ms)}"))
                await _bind_tenant(conn, self.tenant_id)
                yield conn
            finally:
                # Toujours annuler. Il n'y a rien à valider dans une transaction
                # en lecture seule, et annuler rend ce fait structurel.
                await transaction.rollback()


class Databases:
    """Les deux moteurs, tenus ensemble pour qu'aucun ne serve l'usage de l'autre."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.app_engine: AsyncEngine = create_async_engine(
            _normalise_dsn(settings.database_url),
            pool_size=settings.db_pool_size,
            pool_pre_ping=True,
            pool_recycle=1800,
            echo=settings.db_echo,
        )
        self.analytics_engine: AsyncEngine = create_async_engine(
            _normalise_dsn(settings.analytics_database_url),
            pool_size=settings.db_pool_size,
            pool_pre_ping=True,
            pool_recycle=1800,
            echo=settings.db_echo,
            connect_args={
                "server_settings": {
                    "application_name": "atlas-analytics",
                    # Ceinture et bretelles : même une connexion qui sauterait
                    # le `SET` par transaction démarre en lecture seule.
                    "default_transaction_read_only": "on",
                }
            },
        )
        self._app_sessionmaker: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self.app_engine, expire_on_commit=False, class_=AsyncSession
        )

    def for_tenant(self, tenant_id: UUID) -> TenantSession:
        """Sessions applicatives d'une organisation."""
        return TenantSession(self._app_sessionmaker, tenant_id)

    def analytics_for_tenant(self, tenant_id: UUID) -> AnalyticsScope:
        """Accès analytique d'une organisation."""
        return AnalyticsScope(self.analytics_engine, tenant_id)

    @asynccontextmanager
    async def unscoped_session(self) -> AsyncIterator[AsyncSession]:
        """Session **sans** organisation, pour l'authentification et l'audit.

        Nommée pour être visible en relecture. Deux usages légitimes, et deux
        seulement : trouver l'utilisateur qui se connecte (avant de savoir à
        quelle organisation il appartient) et écrire le journal d'audit hors de
        la transaction métier. Les politiques RLS restent actives : ce qui est
        absent est le GUC, donc ces chemins ne peuvent lire aucune table
        soumise à l'isolation sans poser explicitement une organisation.
        """
        async with self._app_sessionmaker() as session:
            yield session

    async def dispose(self) -> None:
        await self.app_engine.dispose()
        await self.analytics_engine.dispose()
