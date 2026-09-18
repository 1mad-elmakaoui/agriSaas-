"""Accès aux données, borné à une organisation par construction.

Le point critique : il n'existe **aucune méthode** permettant d'omettre le
filtre d'organisation. Une fuite inter-organisations demanderait d'écrire
délibérément une requête hors de ce dépôt — ce qui se voit en relecture — et
échouerait tout de même sur la politique RLS.

C'est volontairement redondant avec la RLS. Les deux couches échouent
différemment : le dépôt donne un message exploitable et une trace d'audit, la
RLS tient quand le dépôt est le bug. Retirer l'une parce que l'autre existe,
c'est garder la moins bonne des deux erreurs.

**Ce que la RLS coûte, et qu'il faut assumer.** `atlasagri` distinguait
« ressource inexistante » de « ressource d'une autre organisation », et comptait
les secondes comme tentatives. Sous RLS, la ligne d'une autre organisation est
simplement invisible : la distinction disparaît, et la reconstituer exigerait
une lecture hors politique — c'est-à-dire de rouvrir le trou. Les deux cas
deviennent donc un 404, et l'audit enregistre une consultation infructueuse sur
un identifiant bien formé, ce qui reste dénombrable.
"""

from __future__ import annotations

from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.security import RequestContext
from app.db.base import Tenant, TenantScoped

ModelT = TypeVar("ModelT", bound=TenantScoped)

__all__ = ["TenantRepository"]

_LABELS_FR: dict[str, str] = {
    "User": "Utilisateur",
    "Site": "Site",
    "AuditLog": "Entrée du journal",
}


def _label_fr(model: type[Any]) -> str:
    return _LABELS_FR.get(model.__name__, model.__name__)


class TenantRepository:
    """Dépôt générique borné à l'organisation du contexte appelant."""

    def __init__(self, session: AsyncSession, context: RequestContext) -> None:
        self.session = session
        self.context = context

    # -- primitives -------------------------------------------------------

    def _scoped(self, model: type[ModelT]) -> Select[tuple[ModelT]]:
        if not issubclass(model, TenantScoped):
            raise TypeError(
                f"{model.__name__} n'est pas une table multi-tenant : "
                "elle ne peut pas être interrogée via TenantRepository."
            )
        return select(model).where(model.tenant_id == self.context.tenant_id)

    async def list(self, model: type[ModelT], *conditions: Any) -> list[ModelT]:
        stmt = self._scoped(model)
        for condition in conditions:
            stmt = stmt.where(condition)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def find(self, model: type[ModelT], *conditions: Any) -> ModelT | None:
        stmt = self._scoped(model).limit(1)
        for condition in conditions:
            stmt = stmt.where(condition)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get(self, model: type[ModelT], entity_id: UUID) -> ModelT:
        entity = await self.find(model, model.__table__.c.id == entity_id)  # type: ignore[attr-defined]
        if entity is None:
            raise NotFoundError(f"{_label_fr(model)} introuvable.")
        return entity

    async def add(self, entity: ModelT) -> ModelT:
        """Force l'organisation de l'appelant : une valeur fournie est écrasée.

        Écraser plutôt que valider est délibéré. Valider laisserait un appelant
        découvrir, par le message d'erreur, qu'un identifiant d'organisation
        existe.
        """
        entity.tenant_id = self.context.tenant_id
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def count(self, model: type[ModelT], *conditions: Any) -> int:
        from sqlalchemy import func

        stmt = select(func.count()).select_from(model).where(
            model.tenant_id == self.context.tenant_id
        )
        for condition in conditions:
            stmt = stmt.where(condition)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())


    async def current_tenant(self) -> Tenant:
        """L'organisation de l'appelant, lue sous politique.

        `Tenant` n'a pas de colonne `tenant_id` : elle *est* la ligne
        d'organisation, donc `_scoped` ne s'y applique pas. La politique la
        filtre quand même sur son propre identifiant, et la lecture ne peut
        porter que sur celle de l'appelant.
        """
        row = (
            await self.session.execute(
                select(Tenant).where(Tenant.id == self.context.tenant_id)
            )
        ).scalar_one_or_none()
        if row is None:
            # Un contexte authentifié dont l'organisation a disparu : le jeton
            # est valide, l'organisation ne l'est plus.
            raise NotFoundError(
                "Organisation introuvable.",
                remedy_fr="Reconnectez-vous ou contactez votre administrateur.",
            )
        return row
