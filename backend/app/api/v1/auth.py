"""Point d'entrée d'authentification."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import (
    CurrentAudit,
    CurrentContext,
    CurrentSettings,
    SignupRateLimit,
    get_databases,
)
from app.api.schemas import LoginRequest, SignupRequest, TokenResponse, UserOut
from app.core.errors import AuthenticationError
from app.db.session import Databases
from app.services.auth_service import AuthService
from app.services.signup_service import SignupService

router = APIRouter(prefix="/auth", tags=["Authentification"])


@router.post("/login", response_model=TokenResponse, summary="Ouvrir une session")
async def login(
    payload: LoginRequest,
    settings: CurrentSettings,
    audit: CurrentAudit,
    databases: Annotated[Databases, Depends(get_databases)],
) -> TokenResponse:
    service = AuthService(
        databases, jwt_secret=settings.jwt_secret, ttl_minutes=settings.jwt_ttl_minutes
    )
    try:
        result = await service.authenticate(payload.email, payload.password)
    except AuthenticationError:
        # L'échec n'est pas audité par organisation : on ne sait pas à laquelle
        # l'appelant appartient, et le supposer depuis le courriel écrirait dans
        # le journal d'une organisation sur la foi d'une entrée non prouvée.
        raise

    await audit.record(
        result.context,
        action="auth:login",
        resource_type="SESSION",
        outcome="SUCCESS",
    )
    return TokenResponse(
        access_token=result.token,
        expires_in_minutes=result.ttl_minutes,
        role=result.context.role.value,
        role_label_fr=result.context.role.label_fr,
        tenant_name=result.tenant_name,
        is_demo=result.is_demo,
    )


@router.post(
    "/inscription",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une organisation",
    dependencies=[SignupRateLimit],
)
async def signup(
    payload: SignupRequest,
    settings: CurrentSettings,
    audit: CurrentAudit,
    databases: Annotated[Databases, Depends(get_databases)],
) -> TokenResponse:
    """Inscription autonome (§6).

    L'organisation créée est **vide** : ni site, ni parcelle, ni donnée
    fabriquée. Le référentiel agronomique est global et déjà là — rien n'est
    copié pour elle, et rien n'est inventé à sa place.

    La session est ouverte immédiatement : demander de se reconnecter juste
    après avoir saisi son mot de passe ajoute une étape à un parcours dont la
    promesse est une durée.
    """
    service = SignupService(
        databases, jwt_secret=settings.jwt_secret, ttl_minutes=settings.jwt_ttl_minutes
    )
    result = await service.create_organisation(
        organisation_name=payload.organisation_name,
        full_name=payload.full_name,
        email=payload.email,
        password=payload.password,
        region_code=payload.region_code,
    )
    await audit.record(
        result.context,
        action="organisation:create",
        resource_type="TENANT",
        resource_id=str(result.context.tenant_id),
        outcome="SUCCESS",
    )
    return TokenResponse(
        access_token=result.token,
        expires_in_minutes=result.ttl_minutes,
        role=result.context.role.value,
        role_label_fr=result.context.role.label_fr,
        tenant_name=result.tenant_name,
        is_demo=result.is_demo,
    )


@router.get("/moi", response_model=UserOut, summary="Utilisateur connecté")
async def me(context: CurrentContext) -> UserOut:
    return UserOut(
        id=context.user_id,
        email=context.email,
        full_name=context.email,
        role=context.role.value,
        role_label_fr=context.role.label_fr,
        is_active=True,
    )
