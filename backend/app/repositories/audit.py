"""Journal d'audit — écrit hors de la transaction métier.

`atlasagri` validait le journal au milieu de l'exécution d'un outil, sur la
session de la requête. Cela validait aussi les écritures partielles de la
transaction englobante : une opération interrompue laissait derrière elle un
état à moitié appliqué, difficile à distinguer d'un succès.

Ici, l'audit ouvre sa propre session. Conséquences assumées, dans cet ordre :
une trace subsiste même quand la transaction métier est annulée — ce qui est le
comportement voulu pour une tentative refusée — et **un échec d'écriture du
journal ne fait jamais échouer l'action auditée**.
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.core.logging import current_run_id, get_logger
from app.core.security import RequestContext
from app.db.base import AuditLog
from app.db.session import TENANT_GUC, Databases

logger = get_logger(__name__)

__all__ = ["AuditWriter"]


class AuditWriter:
    def __init__(self, databases: Databases) -> None:
        self._databases = databases

    async def record(
        self,
        context: RequestContext,
        *,
        action: str,
        resource_type: str,
        outcome: str,
        resource_id: str | None = None,
        detail: dict[str, Any] | None = None,
        note: str | None = None,
    ) -> None:
        try:
            async with self._databases.unscoped_session() as session, session.begin():
                conn = await session.connection()
                await conn.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                    {"tenant": str(context.tenant_id)},
                )
                session.add(
                    AuditLog(
                        id=uuid.uuid4(),
                        tenant_id=context.tenant_id,
                        run_id=current_run_id(),
                        actor_user_id=context.user_id,
                        actor_email=context.email,
                        action=action,
                        resource_type=resource_type,
                        resource_id=resource_id,
                        outcome=outcome,
                        detail=detail,
                        note=note,
                    )
                )
        except Exception:
            logger.warning(
                "audit_write_failed", action=action, resource_type=resource_type
            )

    async def record_anonymous(
        self,
        tenant_id: UUID,
        *,
        action: str,
        resource_type: str,
        outcome: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Trace un évènement sans utilisateur identifié — une connexion refusée,
        par exemple, où l'identité revendiquée n'est pas encore prouvée."""
        try:
            async with self._databases.unscoped_session() as session, session.begin():
                conn = await session.connection()
                await conn.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :tenant, true)"),
                    {"tenant": str(tenant_id)},
                )
                session.add(
                    AuditLog(
                        id=uuid.uuid4(),
                        tenant_id=tenant_id,
                        run_id=current_run_id(),
                        action=action,
                        resource_type=resource_type,
                        outcome=outcome,
                        detail=detail,
                    )
                )
        except Exception:
            logger.warning("audit_write_failed", action=action)
