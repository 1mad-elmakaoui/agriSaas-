"""Inscription autonome : créer une organisation et son premier administrateur.

La §6 demande un parcours sans intermédiaire. Ce que ce module crée est donc une
organisation **vide** : ni site, ni parcelle, ni donnée fabriquée. Le référentiel
agronomique, lui, est global et déjà là — une nouvelle organisation lit les
cultures et les sols FAO sans que rien soit copié pour elle, et peut poser ses
propres valeurs mesurées par-dessus le jour où elle en a.

Trois points qui se décident ici, et pas ailleurs :

**Le plan de départ est le plus petit.** Provisionner mieux à l'inscription
reviendrait à vendre sans qu'un administrateur l'ait décidé, ce que la §6
interdit explicitement.

**Un compte créé ici est administrateur.** Il est le seul de son organisation :
lui donner moins produirait une organisation que personne ne peut administrer.

**Le courriel déjà pris est dit tel quel.** C'est une énumération de comptes, et
c'est assumé : un formulaire d'inscription qui échoue en silence est inutilisable,
et la personne concernée doit savoir qu'elle a déjà un accès. La route de
connexion, elle, continue de ne jamais distinguer ses causes d'échec.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError

from app.core.errors import ValidationError
from app.core.logging import get_logger
from app.db.base import Tenant
from app.db.session import Databases
from app.domain.enums import UserRole
from app.services.auth_service import AuthResult, AuthService

logger = get_logger(__name__)

__all__ = ["SignupService", "slugify"]

#: Plan attribué à l'inscription. Le plus petit, délibérément.
INITIAL_PLAN = "COOPERATIVE"


def slugify(name: str) -> str:
    """Identifiant lisible tiré du nom, translittération comprise.

    « Coopérative Aït Melloul » donne `cooperative-ait-melloul`. Sans
    translittération, les accents disparaîtraient purement et simplement et le
    nom deviendrait méconnaissable dans une URL — ce qui compte pour un
    identifiant que l'administrateur devra un jour recopier pour confirmer une
    suppression.
    """
    folded = (
        name.strip()
        .lower()
        .translate(
            str.maketrans(
                "àâäáãåçéèêëíìîïñóòôöõúùûüýÿ",
                "aaaaaaceeeeiiiinooooouuuuyy",
            )
        )
    )
    slug = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    return slug[:60] or "organisation"


class SignupService:
    def __init__(
        self, databases: Databases, *, jwt_secret: str, ttl_minutes: int
    ) -> None:
        self._databases = databases
        self._auth = AuthService(
            databases, jwt_secret=jwt_secret, ttl_minutes=ttl_minutes
        )

    async def create_organisation(
        self,
        *,
        organisation_name: str,
        full_name: str,
        email: str,
        password: str,
        region_code: str | None = None,
    ) -> AuthResult:
        normalised = email.strip().lower()
        if await self._email_taken(normalised):
            raise ValidationError(
                "Ce courriel est déjà rattaché à une organisation.",
                remedy_fr="Connectez-vous, ou inscrivez-vous avec une autre adresse.",
            )

        tenant_id = uuid.uuid4()
        slug = await self._insert_tenant(
            tenant_id=tenant_id,
            name=organisation_name.strip(),
            base_slug=slugify(organisation_name),
            region_code=region_code,
        )

        await self._auth.provision_user(
            tenant_id=tenant_id,
            email=normalised,
            full_name=full_name.strip(),
            password=password,
            role=UserRole.ADMIN,
        )
        logger.info("organisation_created", tenant=str(tenant_id), slug=slug)

        # Connecté immédiatement : demander de se reconnecter juste après avoir
        # saisi son mot de passe ajoute une étape à un parcours dont la promesse
        # est une durée.
        return await self._auth.authenticate(normalised, password)

    async def _email_taken(self, email: str) -> bool:
        async with self._databases.unscoped_session() as session:
            found = (
                await session.execute(
                    sql_text("SELECT 1 FROM app.user_directory WHERE email = :e"),
                    {"e": email},
                )
            ).scalar_one_or_none()
        return found is not None

    async def _insert_tenant(
        self, *, tenant_id: uuid.UUID, name: str, base_slug: str, region_code: str | None
    ) -> str:
        """Écrit l'organisation, en laissant la contrainte trancher l'unicité.

        Deux coopératives peuvent légitimement porter le même nom. Interroger la
        table avant d'écrire donnerait une réponse qui peut être fausse au moment
        de l'écriture — et de toute façon, l'organisation qui s'inscrit ne peut
        pas *lire* la ligne d'une autre : la politique RLS le lui interdit, ce
        qui est exactement ce qu'on veut. C'est donc la contrainte d'unicité qui
        décide, et le suffixe n'arrive que si elle a parlé.
        """
        for attempt in range(6):
            slug = base_slug if attempt == 0 else f"{base_slug}-{uuid.uuid4().hex[:6]}"
            try:
                async with self._databases.for_tenant(tenant_id).begin() as session:
                    session.add(
                        Tenant(
                            id=tenant_id,
                            name=name,
                            slug=slug,
                            region_code=region_code,
                            plan_code=INITIAL_PLAN,
                            # Une organisation réelle n'est jamais une
                            # démonstration : la bannière « aucune valeur n'est
                            # une mesure » serait un mensonge sur ses relevés.
                            is_demo=False,
                        )
                    )
            except IntegrityError:
                continue
            return slug
        raise ValidationError(
            "Impossible d'attribuer un identifiant à cette organisation.",
            remedy_fr="Réessayez avec un nom légèrement différent.",
        )
