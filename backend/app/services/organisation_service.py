"""Organisation, journal d'accès, export et effacement (loi 09-08 / CNDP).

Trois droits de la loi 09-08 ont une traduction technique, et c'est ici qu'elle
vit : **accès** (savoir ce qui est détenu), **portabilité** (en obtenir une
copie exploitable) et **suppression** (obtenir l'effacement). Le quatrième — la
traçabilité des consultations — est le journal d'audit, écrit ailleurs et lu
ici.

Deux choix qui se discutent, et qui sont donc écrits :

* **l'export est découvert, pas énuméré.** Il parcourt les tables porteuses de
  `tenant_id` telles que le modèle les déclare. Une liste écrite à la main
  omettrait la table ajoutée le mois prochain, et un export incomplet présenté
  comme complet est précisément le manquement que la loi sanctionne ;
* **l'effacement d'une personne n'efface pas le journal.** Le journal est la
  preuve de qui a vu quoi : le supprimer effacerait aussi la trace des accès
  subis par d'autres. Le courriel y est donc remplacé par un pseudonyme, et
  l'identifiant technique subsiste sans pouvoir être rattaché à quiconque.
  C'est une atténuation, pas un effacement complet, et `docs/conformite.md` le
  dit en ces termes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Table, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthorizationError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import RequestContext
from app.db.base import Base, tables_with_tenant_column
from app.domain.enums import UserRole

logger = get_logger(__name__)

__all__ = ["AuditEntry", "MemberSummary", "OrganisationService"]

#: Colonnes jamais exportées. Un condensat de mot de passe n'est la donnée
#: personnelle de personne : c'est un secret d'authentification, et l'inclure
#: dans un fichier que l'utilisateur transporte lui-même créerait un risque que
#: la portabilité n'exige pas.
REDACTED_COLUMNS: frozenset[str] = frozenset({"password_hash"})

#: Tables exclues de l'export : le journal d'audit part dans son propre flux
#: (`/journal`), avec sa propre autorisation. Le mêler à l'export donnerait à
#: quiconque peut exporter la liste des consultations de ses collègues.
EXPORT_EXCLUDED_TABLES: frozenset[str] = frozenset({"audit_log"})


@dataclass(frozen=True, slots=True)
class MemberSummary:
    id: uuid.UUID
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OrganisationProfile:
    id: uuid.UUID
    name: str
    slug: str
    region_code: str | None
    plan_code: str
    is_demo: bool
    members: list[MemberSummary]


@dataclass(frozen=True, slots=True)
class AuditEntry:
    occurred_at: datetime
    actor_email: str | None
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    run_id: str | None
    detail: dict[str, Any] | None


class OrganisationService:
    def __init__(self, session: AsyncSession, context: RequestContext) -> None:
        self._session = session
        self._context = context

    # -- lecture ------------------------------------------------------------

    async def profile(self) -> OrganisationProfile:
        """Fiche de l'organisation et de ses membres, en une lecture."""
        row = (
            await self._session.execute(
                text(
                    "SELECT id, name, slug, region_code, plan_code, is_demo "
                    "FROM app.tenants WHERE id = :id"
                ),
                {"id": self._context.tenant_id},
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("Organisation introuvable.")
        return OrganisationProfile(
            id=row[0],
            name=row[1],
            slug=row[2],
            region_code=row[3],
            plan_code=row[4],
            is_demo=row[5],
            members=await self.members(),
        )

    async def members(self) -> list[MemberSummary]:
        rows = (
            await self._session.execute(
                text(
                    "SELECT id, email, full_name, role, is_active, created_at "
                    "FROM app.users ORDER BY full_name"
                )
            )
        ).all()
        return [
            MemberSummary(
                id=row[0],
                email=row[1],
                full_name=row[2],
                role=UserRole(row[3]),
                is_active=row[4],
                created_at=row[5],
            )
            for row in rows
        ]

    async def audit_entries(self, *, limit: int = 100) -> list[AuditEntry]:
        """Les dernières lignes du journal de l'organisation.

        Lire ce journal est soi-même un accès aux données d'autrui : l'appelant
        y voit ce que ses collègues ont consulté. La route qui appelle cette
        méthode l'inscrit donc elle-même au journal — un registre qu'on peut
        consulter sans laisser de trace ne prouve plus rien.
        """
        rows = (
            await self._session.execute(
                text(
                    "SELECT occurred_at, actor_email, action, resource_type, "
                    "resource_id, outcome, run_id, detail FROM app.audit_log "
                    "ORDER BY occurred_at DESC LIMIT :limit"
                ),
                {"limit": min(max(limit, 1), 500)},
            )
        ).all()
        return [
            AuditEntry(
                occurred_at=row[0],
                actor_email=row[1],
                action=row[2],
                resource_type=row[3],
                resource_id=row[4],
                outcome=row[5],
                run_id=row[6],
                detail=row[7],
            )
            for row in rows
        ]

    # -- portabilité ---------------------------------------------------------

    async def export(self) -> dict[str, Any]:
        """Copie complète des données de l'organisation, table par table.

        Les tables sont **découvertes** depuis le modèle : celle qu'on ajoutera
        se retrouve dans l'export le jour de sa création, sans que personne ait
        à y penser. C'est la seule façon qu'un export reste complet.
        """
        tables = [
            name
            for name in tables_with_tenant_column()
            if name not in EXPORT_EXCLUDED_TABLES
        ]
        content: dict[str, list[dict[str, Any]]] = {}
        redacted: set[str] = set()

        for table in tables:
            # Requête construite depuis l'objet `Table` du modèle, jamais par
            # interpolation d'un nom dans du SQL : le nom vient certes du
            # modèle, mais une chaîne formatée finit toujours par accepter, un
            # jour, une variable qui vient d'ailleurs.
            #
            # Le filtre `tenant_id` est explicite plutôt que laissé à RLS : les
            # tables de référentiel laissent passer la ligne globale
            # (`tenant_id IS NULL`), et l'export d'une coopérative contiendrait
            # sinon tout le catalogue FAO présenté comme ses données.
            entity = self._table(table)
            result = await self._session.execute(
                select(entity).where(entity.c.tenant_id == self._context.tenant_id)
            )
            columns = list(result.keys())
            rows = []
            for row in result.all():
                record = {}
                for column, value in zip(columns, row, strict=True):
                    if column in REDACTED_COLUMNS:
                        redacted.add(f"{table}.{column}")
                        continue
                    record[column] = _jsonable(value)
                rows.append(record)
            content[table] = rows

        return {
            "organisation_id": str(self._context.tenant_id),
            "exported_at": datetime.now().astimezone().isoformat(),
            "exported_by": self._context.email,
            "tables": content,
            "row_counts": {name: len(rows) for name, rows in content.items()},
            # Dit dans l'export lui-même, pas seulement dans une page d'aide :
            # un fichier qui circule doit porter ses propres réserves.
            "excluded_tables": sorted(EXPORT_EXCLUDED_TABLES),
            "redacted_columns": sorted(redacted),
            "notice_fr": (
                "Export complet des données de votre organisation, hors journal "
                "d'audit — consultable séparément. Les secrets "
                "d'authentification sont retirés : ce ne sont pas des données "
                "personnelles mais des moyens d'accès."
            ),
        }

    # -- effacement ----------------------------------------------------------

    async def delete_member(self, user_id: uuid.UUID) -> str:
        """Efface une personne, et pseudonymise sa trace au journal.

        Le journal subsiste : il est la preuve des accès subis par les autres,
        et l'effacer au nom du droit de l'un retirerait aux autres le leur.
        Le courriel y est remplacé ; l'identifiant technique reste, sans plus
        rien à quoi le rattacher.
        """
        row = (
            await self._session.execute(
                text("SELECT email, role FROM app.users WHERE id = :id"),
                {"id": user_id},
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError(
                "Cet utilisateur n'existe pas dans votre organisation.",
                remedy_fr="Vérifiez la liste des membres.",
            )
        email, role = str(row[0]), UserRole(row[1])

        if user_id == self._context.user_id:
            raise ValidationError(
                "Vous ne pouvez pas supprimer votre propre compte.",
                remedy_fr=(
                    "Demandez à un autre administrateur de le faire, pour que "
                    "l'organisation ne se retrouve pas sans administrateur."
                ),
            )
        if role is UserRole.ADMIN and await self._admin_count() <= 1:
            raise ValidationError(
                "C'est le dernier administrateur de l'organisation.",
                remedy_fr="Nommez un autre administrateur avant de supprimer celui-ci.",
            )

        pseudonym = f"supprimé-{user_id.hex[:8]}"
        await self._session.execute(
            text(
                "UPDATE app.audit_log SET actor_email = :pseudo "
                "WHERE actor_user_id = :id"
            ),
            {"pseudo": pseudonym, "id": user_id},
        )
        await self._session.execute(
            text("DELETE FROM app.user_directory WHERE email = :email"),
            {"email": email},
        )
        await self._session.execute(
            text("DELETE FROM app.users WHERE id = :id"), {"id": user_id}
        )
        logger.info(
            "member_deleted",
            tenant=str(self._context.tenant_id),
            user=str(user_id),
        )
        return email

    async def delete_organisation(self, confirmation: str) -> dict[str, int]:
        """Efface **toutes** les données de l'organisation. Irréversible.

        La confirmation demandée est l'identifiant lisible de l'organisation,
        recopié à la main. Une case à cocher se coche par réflexe ; un nom se
        recopie en ayant lu ce qu'on tape.

        Le journal d'audit part avec le reste : conservé, il désignerait par
        courriel des personnes dont on vient d'effacer le compte, dans une
        organisation qui n'existe plus pour en répondre.
        """
        slug = (
            await self._session.execute(
                text("SELECT slug FROM app.tenants WHERE id = :id"),
                {"id": self._context.tenant_id},
            )
        ).scalar_one()
        if confirmation.strip() != slug:
            raise ValidationError(
                "La confirmation ne correspond pas à l'identifiant de l'organisation.",
                remedy_fr=f"Recopiez exactement « {slug} » pour confirmer la suppression.",
            )

        deleted: dict[str, int] = {}
        # L'ordre inverse des dépendances : les enfants d'abord. Les clés
        # étrangères en cascade suffiraient depuis `tenants`, mais compter ce
        # qui part table par table est ce qui permet de le prouver ensuite.
        for table in reversed(tables_with_tenant_column()):
            # Le filtre est explicite pour deux raisons, et chacune suffirait :
            # les tables de référentiel laissent passer la ligne globale, qu'un
            # `DELETE` sans clause effacerait **pour toutes les organisations** ;
            # et `user_directory` est exemptée de RLS par conception.
            entity = self._table(table)
            result = await self._session.execute(
                delete(entity).where(entity.c.tenant_id == self._context.tenant_id)
            )
            # `rowcount` n'existe que sur le curseur ; l'annotation de
            # SQLAlchemy ne le dit pas pour un `Result` générique.
            deleted[table] = int(getattr(result, "rowcount", 0) or 0)

        await self._session.execute(
            text("DELETE FROM app.tenants WHERE id = :id"),
            {"id": self._context.tenant_id},
        )
        logger.warning("organisation_deleted", tenant=str(self._context.tenant_id))
        return deleted

    async def set_role(self, user_id: uuid.UUID, role: UserRole) -> None:
        """Change le rôle d'un membre.

        Refuse de retirer le dernier administrateur : une organisation sans
        administrateur ne peut plus ni inviter, ni changer de plan, ni se
        supprimer — elle ne peut qu'appeler au secours.
        """
        current = (
            await self._session.execute(
                text("SELECT role FROM app.users WHERE id = :id"), {"id": user_id}
            )
        ).scalar_one_or_none()
        if current is None:
            raise NotFoundError(
                "Cet utilisateur n'existe pas dans votre organisation.",
                remedy_fr="Vérifiez la liste des membres.",
            )
        if (
            UserRole(current) is UserRole.ADMIN
            and role is not UserRole.ADMIN
            and await self._admin_count() <= 1
        ):
            raise ValidationError(
                "C'est le dernier administrateur de l'organisation.",
                remedy_fr="Nommez un autre administrateur avant de changer celui-ci.",
            )
        await self._session.execute(
            text("UPDATE app.users SET role = :role WHERE id = :id"),
            {"role": role.value, "id": user_id},
        )

    @staticmethod
    def _table(name: str) -> Table:
        """L'objet `Table` du modèle, par son nom.

        Le passage par les métadonnées est ce qui rend l'export et l'effacement
        à la fois découverts *et* sans SQL construit par chaîne.
        """
        return Base.metadata.tables[f"app.{name}"]

    async def _admin_count(self) -> int:
        return int(
            (
                await self._session.execute(
                    text(
                        "SELECT count(*) FROM app.users "
                        "WHERE role = 'ADMIN' AND is_active"
                    )
                )
            ).scalar_one()
        )


def require_admin(context: RequestContext, act_fr: str) -> None:
    """Refuse l'acte à qui n'est pas administrateur, en le nommant.

    Rendu ici plutôt qu'à chaque route : trois formulations différentes du même
    refus finiraient par diverger, et l'une d'elles oublierait de dire à qui
    s'adresser.
    """
    if context.role is not UserRole.ADMIN:
        raise AuthorizationError(
            f"Seul un administrateur peut {act_fr}.",
            remedy_fr="Demandez à un administrateur de votre organisation.",
        )


def _jsonable(value: Any) -> Any:
    """Rend une valeur sérialisable sans en changer le sens.

    Les dates partent en ISO 8601 et les identifiants en chaîne ; rien n'est
    arrondi, rien n'est reformaté. Un export est une copie, pas une
    présentation.
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
