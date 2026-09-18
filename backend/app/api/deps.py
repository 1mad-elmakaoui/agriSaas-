"""Dépendances FastAPI — c'est ici que le tenant entre dans le système.

Le `RequestContext` est construit **uniquement** depuis un jeton signé. Il n'y a
aucun chemin par lequel un en-tête, un paramètre de requête, un corps JSON ou un
argument d'outil puisse en fournir un. C'est la forme concrète de la règle : le
modèle de langage ne peut pas franchir une frontière d'organisation parce qu'il
n'a aucun moyen d'exprimer la demande.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AuthenticationError, RateLimitedError
from app.core.logging import get_logger
from app.core.rate_limit import AGENT_LIMIT, SIGNUP_LIMIT, RateLimiter
from app.core.security import RequestContext, decode_access_token
from app.db.session import AnalyticsScope, Databases
from app.llm.base import LLMProvider
from app.repositories.audit import AuditWriter
from app.repositories.tenant import TenantRepository

logger = get_logger(__name__)

#: `auto_error=False` : on veut notre propre message français et notre propre
#: code, pas le « Not authenticated » de la bibliothèque.
_bearer = HTTPBearer(auto_error=False)


def get_settings_from_app(request: Request) -> Settings:
    """Réglages **de cette application**, pas les réglages globaux en cache.

    `create_app(settings)` sert à construire une application configurée
    autrement — une suite de tests, une seconde instance. Lire ici le cache
    global de `get_settings()` ferait que l'objet passé serait ignoré par toute
    dépendance, silencieusement : l'application se construirait avec la bonne
    configuration et en utiliserait une autre. C'est ainsi qu'un secret de test
    et un secret de production peuvent coexister sans que rien ne le signale.
    """
    settings: Settings = request.app.state.settings
    return settings


def get_databases(request: Request) -> Databases:
    databases: Databases = request.app.state.databases
    return databases


def get_audit(request: Request) -> AuditWriter:
    audit: AuditWriter = request.app.state.audit
    return audit


async def get_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    settings: Annotated[Settings, Depends(get_settings_from_app)],
) -> RequestContext:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError(
            "Authentification requise.", remedy_fr="Connectez-vous pour continuer."
        )
    return decode_access_token(credentials.credentials, secret=settings.jwt_secret)


async def get_session(
    context: Annotated[RequestContext, Depends(get_context)],
    databases: Annotated[Databases, Depends(get_databases)],
) -> AsyncIterator[AsyncSession]:
    """Session déjà liée à l'organisation de l'appelant.

    Il n'existe pas de variante non liée exposée à l'API : la seule façon
    d'obtenir une session ici passe par un contexte authentifié.
    """
    async with databases.for_tenant(context.tenant_id).begin() as session:
        yield session


async def get_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
    context: Annotated[RequestContext, Depends(get_context)],
) -> TenantRepository:
    return TenantRepository(session, context)


async def get_analytics_scope(
    context: Annotated[RequestContext, Depends(get_context)],
    databases: Annotated[Databases, Depends(get_databases)],
) -> AnalyticsScope:
    """Portée analytique de l'appelant.

    Rendue par dépendance plutôt que construite à l'usage : un exécuteur SQL
    ne peut être construit qu'à partir d'une portée, et une portée ne peut être
    obtenue qu'à partir d'un contexte authentifié. La chaîne ne comporte aucun
    maillon où le tenant serait facultatif.
    """
    return databases.analytics_for_tenant(context.tenant_id)


def get_llm_provider(
    settings: Annotated[Settings, Depends(get_settings_from_app)],
) -> LLMProvider | None:
    """Fournisseur de modèle, ou `None` quand l'installation n'en a pas.

    Rendu par dépendance plutôt que construit dans la route pour une raison de
    vérifiabilité : la boucle d'agent doit pouvoir être exercée de bout en bout,
    à travers le vrai routeur, avec un fournisseur scripté. Construit dans la
    route, le chemin HTTP resterait non testé et seule la classe le serait.

    Ce n'est pas une porte de simulation : aucun réglage ne permet d'installer un
    fournisseur scripté en exécution — `Settings._production_guards` refuse
    `llm_provider = "fake"` hors des environnements de développement, et seule
    une surcharge de dépendance, impossible à formuler par configuration, peut
    en substituer un.
    """
    from app.llm.anthropic_provider import AnthropicProvider

    if settings.llm_provider == "fake" or not settings.anthropic_api_key:
        return None
    return AnthropicProvider(settings.anthropic_api_key, settings.llm_model)


def get_rate_limiter(request: Request) -> RateLimiter:
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


async def enforce_agent_rate_limit(
    context: Annotated[RequestContext, Depends(get_context)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> None:
    """Seuil strict du copilote et de l'analyse (§9).

    Posé en dépendance de routeur plutôt qu'en intergiciel : l'intergiciel ne
    connaît pas l'utilisateur, et une limite de modèle par adresse punirait
    toute une exploitation derrière une seule sortie internet pour l'usage d'une
    personne.

    La clé est l'utilisateur et non l'organisation : un analyste qui boucle ne
    doit pas fermer le copilote à ses collègues.
    """
    verdict = limiter.check(f"agent:{context.user_id}", AGENT_LIMIT)
    if verdict.allowed:
        return
    logger.warning(
        "rate_limited",
        scope="agent",
        tenant=str(context.tenant_id),
        user=str(context.user_id),
    )
    raise RateLimitedError(
        verdict.message_fr,
        remedy_fr=verdict.remedy_fr,
        retry_after=max(1, round(verdict.retry_after_seconds)),
    )


async def enforce_signup_rate_limit(
    request: Request,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> None:
    """Seuil de l'inscription, par adresse cliente.

    Sur l'adresse, parce qu'il n'y a par définition aucun utilisateur à qui
    l'attribuer. Beaucoup plus strict que le seuil général : un appel réussi ici
    crée une organisation, et un script en créerait des milliers avant que
    quiconque s'en aperçoive.
    """
    client = request.client.host if request.client else "inconnu"
    verdict = limiter.check(f"signup:{client}", SIGNUP_LIMIT)
    if verdict.allowed:
        return
    logger.warning("rate_limited", scope="signup", client=client)
    raise RateLimitedError(
        verdict.message_fr,
        remedy_fr=verdict.remedy_fr,
        retry_after=max(1, round(verdict.retry_after_seconds)),
    )


CurrentContext = Annotated[RequestContext, Depends(get_context)]
CurrentRepository = Annotated[TenantRepository, Depends(get_repository)]
CurrentSession = Annotated[AsyncSession, Depends(get_session)]
CurrentAudit = Annotated[AuditWriter, Depends(get_audit)]
CurrentSettings = Annotated[Settings, Depends(get_settings_from_app)]
CurrentAnalytics = Annotated[AnalyticsScope, Depends(get_analytics_scope)]
CurrentProvider = Annotated[LLMProvider | None, Depends(get_llm_provider)]
AgentRateLimit = Depends(enforce_agent_rate_limit)
SignupRateLimit = Depends(enforce_signup_rate_limit)
