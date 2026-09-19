"""Application FastAPI.

Le démarrage exécute les sondes de `db/preflight.py` avant d'accepter la
moindre requête. Une garantie non vérifiée au démarrage est une garantie
découverte absente en production, par l'incident qu'elle devait empêcher.

En production, un échec de sonde **arrête le processus**. En développement, il
est journalisé en erreur et le démarrage continue : refuser de démarrer une base
locale à moitié migrée rendrait la boucle de développement pénible sans rien
protéger. La différence est explicite, jamais implicite.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import (
    analytics,
    auth,
    copilot,
    farm,
    onboarding,
    organisation,
    recommendations,
    sites,
    subscription,
)
from app.core.config import Settings, get_settings
from app.core.errors import AtlasError, ConfigurationError
from app.core.logging import configure_logging, get_logger, new_run_id, run_id_var
from app.core.rate_limit import DEFAULT_LIMIT, RateLimiter
from app.db.preflight import ProbeOutcome, run_preflight
from app.db.session import Databases
from app.repositories.audit import AuditWriter

logger = get_logger(__name__)

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    databases = Databases(settings)
    app.state.databases = databases
    app.state.audit = AuditWriter(databases)

    results = await run_preflight(
        databases.app_engine,
        databases.analytics_engine,
        require_postgis=settings.require_postgis,
    )
    failures = [r for r in results if r.outcome is ProbeOutcome.FAIL]
    if failures and settings.environment == "production":
        await databases.dispose()
        raise ConfigurationError(
            "Refusing to start: preflight probes failed.\n  - "
            + "\n  - ".join(f"{r.name}: {r.detail}" for r in failures)
        )
    if failures:
        logger.error(
            "preflight_failed_non_production",
            count=len(failures),
            probes=[r.name for r in failures],
            detail="The product would refuse to start in production.",
        )

    app.state.preflight = results
    try:
        yield
    finally:
        await databases.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(json_output=resolved.environment != "local")

    app = FastAPI(
        title="AtlasAgri",
        description=(
            "Plateforme de décision pour les exploitations et les chaînes "
            "d'approvisionnement agricoles marocaines."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = resolved
    #: Un limiteur par application, pas un singleton de module : deux
    #: applications construites dans le même processus — la suite de tests en
    #: construit plusieurs — ne doivent pas partager leurs compteurs.
    app.state.rate_limiter = RateLimiter()

    # Le navigateur ne parle jamais à un fournisseur météo, satellite ou de
    # modèle : tout passe par cette API. Les origines autorisées sont donc
    # celles du frontend, et rien d'autre.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _rate_limit_middleware(request: Request, call_next: Any) -> Any:
        """Seuil général, par adresse cliente.

        En intergiciel plutôt qu'en dépendance de routeur : une dépendance se
        pose routeur par routeur, et le routeur qu'on ajoutera dans six mois
        sera celui qu'on aura oublié. Ici, une route nouvelle est couverte le
        jour où elle est écrite.

        La clé est l'adresse cliente, non l'utilisateur : la limite doit
        s'appliquer **avant** l'authentification, sinon une rafale de tentatives
        de connexion ne rencontrerait aucun seuil.
        """
        if request.url.path in {"/api/sante", "/api/docs", "/api/openapi.json"}:
            return await call_next(request)

        client = request.client.host if request.client else "inconnu"
        limiter: RateLimiter = request.app.state.rate_limiter
        verdict = limiter.check(f"ip:{client}", DEFAULT_LIMIT)
        if not verdict.allowed:
            retry_after = max(1, round(verdict.retry_after_seconds))
            logger.warning("rate_limited", scope="default", client=client)
            return JSONResponse(
                status_code=429,
                content={
                    "code": "rate_limited",
                    "message_fr": verdict.message_fr,
                    "remedy_fr": verdict.remedy_fr,
                },
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)

    @app.middleware("http")
    async def _run_id_middleware(request: Request, call_next: Any) -> Any:
        """Un identifiant de corrélation par requête.

        Le même identifiant apparaît dans les journaux, dans le journal d'audit
        et — à partir de la phase 4 — dans la trace d'outils rendue à
        l'interface. C'est ce qui permet de reconstituer une décision des mois
        plus tard.
        """
        token = run_id_var.set(new_run_id())
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Run-Id"] = run_id_var.get() or ""
            # La ligne d'accès est écrite ici et non par uvicorn, dont le
            # journal ne porte ni identifiant de corrélation ni durée. C'est ce
            # qui relie une ligne d'audit — « qui a vu quoi » — à la requête qui
            # l'a produite, des mois plus tard.
            #
            # Aucune chaîne de requête n'est journalisée : elle contient des
            # identifiants de ressources, et rien ne garantit qu'elle ne
            # contiendra jamais autre chose.
            logger.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            return response
        finally:
            run_id_var.reset(token)

    @app.exception_handler(AtlasError)
    async def _atlas_error_handler(_request: Request, exc: AtlasError) -> JSONResponse:
        """Une erreur métier devient une charge utile française exploitable.

        Jamais une trace technique : l'utilisateur reçoit ce qu'il faut faire,
        l'opérateur retrouve le détail par le `run_id` dans les journaux.
        """
        headers = {}
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        return JSONResponse(
            status_code=exc.http_status, content=exc.payload(), headers=headers
        )

    app.include_router(auth.router, prefix=API_PREFIX)
    app.include_router(sites.router, prefix=API_PREFIX)
    app.include_router(copilot.router, prefix=API_PREFIX)
    app.include_router(farm.router, prefix=API_PREFIX)
    app.include_router(onboarding.router, prefix=API_PREFIX)
    app.include_router(organisation.router, prefix=API_PREFIX)
    app.include_router(analytics.router, prefix=API_PREFIX)
    app.include_router(recommendations.router, prefix=API_PREFIX)
    app.include_router(subscription.router, prefix=API_PREFIX)

    @app.get("/api/sante", tags=["Exploitation"], summary="État du service")
    async def health(request: Request) -> dict[str, Any]:
        probes = getattr(request.app.state, "preflight", [])
        healthy = all(p.outcome is not ProbeOutcome.FAIL for p in probes)
        return {
            "statut": "ok" if healthy else "degrade",
            "environnement": resolved.environment,
            "sondes": [
                {"nom": p.name, "resultat": p.outcome.value, "detail": p.detail}
                for p in probes
            ],
        }

    return app
