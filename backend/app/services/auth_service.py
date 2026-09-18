"""Authentification — la traversée de frontière que `FORCE RLS` rend visible.

Pour lire la fiche d'un utilisateur il faut connaître son organisation ; pour
connaître son organisation il faut avoir lu sa fiche. Le produit résout la
boucle par une table d'aiguillage explicitement globale (`user_directory`), qui
ne contient qu'un courriel et une organisation — jamais un secret. L'empreinte
du mot de passe reste dans `users`, sous politique.

Deux détails de sécurité qui ne se voient pas en relecture :

* **Un seul message d'échec.** Courriel inconnu, mot de passe faux, compte
  désactivé : le même texte. Distinguer renseignerait un attaquant sur les
  comptes existants.
* **Un temps de réponse constant.** Sans vérification factice sur un courriel
  inconnu, l'écart de durée entre « inconnu » (retour immédiat) et « connu »
  (un bcrypt à 12 tours) est un oracle mesurable depuis l'extérieur.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy import text as sql_text

from app.core.errors import AuthenticationError
from app.core.logging import get_logger
from app.core.security import (
    RequestContext,
    create_access_token,
    hash_password,
    verify_password,
)
from app.db.base import Tenant, User, UserDirectory
from app.db.session import TENANT_GUC, Databases
from app.domain.enums import UserRole

logger = get_logger(__name__)

__all__ = ["AuthResult", "AuthService"]

#: Empreinte factice, vérifiée quand le courriel est inconnu, pour que le temps
#: de réponse ne dépende pas de l'existence du compte. Calculée une fois au
#: chargement du module, avec le même coût que les empreintes réelles.
_DUMMY_HASH = hash_password("timing-equalisation-only-never-a-real-password")

_GENERIC_FAILURE = "Courriel ou mot de passe incorrect."


class AuthResult:
    __slots__ = ("context", "is_demo", "tenant_name", "token", "ttl_minutes")

    def __init__(
        self,
        *,
        token: str,
        context: RequestContext,
        tenant_name: str,
        is_demo: bool,
        ttl_minutes: int,
    ) -> None:
        self.token = token
        self.context = context
        self.tenant_name = tenant_name
        self.is_demo = is_demo
        self.ttl_minutes = ttl_minutes


class AuthService:
    def __init__(self, databases: Databases, *, jwt_secret: str, ttl_minutes: int) -> None:
        self._databases = databases
        self._secret = jwt_secret
        self._ttl = ttl_minutes

    async def _route(self, email: str) -> tuple[UUID, UUID] | None:
        """Aiguillage courriel → (organisation, utilisateur).

        Seule lecture du produit qui traverse les organisations, et la seule
        raison pour laquelle `user_directory` existe.
        """
        async with self._databases.unscoped_session() as session:
            row = (
                await session.execute(
                    select(UserDirectory.tenant_id, UserDirectory.user_id).where(
                        UserDirectory.email == email.lower()
                    )
                )
            ).first()
        return (row[0], row[1]) if row else None

    async def authenticate(self, email: str, password: str) -> AuthResult:
        route = await self._route(email)
        if route is None:
            # Même coût qu'une vérification réelle : sans cela, la latence dit
            # à l'appelant si le courriel existe.
            verify_password(password, _DUMMY_HASH)
            raise AuthenticationError(_GENERIC_FAILURE)

        tenant_id, user_id = route
        async with self._databases.for_tenant(tenant_id).begin() as session:
            user = await session.get(User, user_id)
            tenant = await session.get(Tenant, tenant_id)
            if user is None or tenant is None:
                # L'aiguillage pointe vers une fiche absente : incohérence de
                # données, pas une erreur de l'utilisateur. On refuse et on le
                # signale à l'exploitant, sans en dire plus à l'appelant.
                logger.error("auth_directory_orphan", tenant_id=str(tenant_id))
                verify_password(password, _DUMMY_HASH)
                raise AuthenticationError(_GENERIC_FAILURE)

            if not verify_password(password, user.password_hash) or not user.is_active:
                raise AuthenticationError(_GENERIC_FAILURE)

            context = RequestContext(
                user_id=user.id,
                tenant_id=tenant.id,
                role=user.role,
                email=user.email,
            )
            return AuthResult(
                token=create_access_token(
                    context, secret=self._secret, ttl_minutes=self._ttl
                ),
                context=context,
                tenant_name=tenant.name,
                is_demo=tenant.is_demo,
                ttl_minutes=self._ttl,
            )

    async def provision_user(
        self,
        *,
        tenant_id: UUID,
        email: str,
        full_name: str,
        password: str,
        role: UserRole,
    ) -> UUID:
        """Crée un utilisateur **et** son entrée d'aiguillage, en une transaction.

        Les deux écritures sont indissociables : une fiche sans aiguillage est
        un compte qui ne peut pas se connecter, un aiguillage sans fiche est une
        connexion qui échoue à mi-chemin. Un test vérifie que les deux tables
        restent en accord.
        """
        import uuid as _uuid

        user_id = _uuid.uuid4()
        normalised = email.lower()
        async with self._databases.for_tenant(tenant_id).begin() as session:
            session.add(
                User(
                    id=user_id,
                    tenant_id=tenant_id,
                    email=normalised,
                    full_name=full_name,
                    password_hash=hash_password(password),
                    role=role,
                )
            )
            # `user_directory` est hors politique : l'écriture passe par la même
            # transaction, donc elle est annulée si la fiche échoue.
            conn = await session.connection()
            await conn.execute(
                sql_text(f"SELECT set_config('{TENANT_GUC}', :t, true)"),
                {"t": str(tenant_id)},
            )
            await conn.execute(
                sql_text(
                    "INSERT INTO app.user_directory (email, user_id, tenant_id) "
                    "VALUES (:email, :user_id, :tenant_id)"
                ),
                {"email": normalised, "user_id": user_id, "tenant_id": tenant_id},
            )
        return user_id
