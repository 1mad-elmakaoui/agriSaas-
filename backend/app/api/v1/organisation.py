"""Console d'administration et droits de la loi 09-08.

Quatre choses vivent ici : qui compose l'organisation, qui a vu quoi, comment
obtenir une copie de ses données, et comment les faire effacer. Les trois
dernières sont des droits opposables — pas des fonctions de confort — et elles
sont donc traitées comme telles : réservées à l'administrateur, inscrites au
journal, et honnêtes sur ce qu'elles ne couvrent pas.

Consulter le journal est **soi-même** un accès aux données d'autrui : cette
consultation s'inscrit donc au journal. Un registre qu'on peut lire sans laisser
de trace ne prouve plus rien.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import (
    CurrentAudit,
    CurrentContext,
    CurrentSession,
    CurrentSettings,
    get_databases,
)
from app.api.schemas import (
    AuditEntryOut,
    ComplianceOut,
    DeletionReceiptOut,
    DeletionRequestIn,
    MemberCreate,
    MemberOut,
    OrganisationOut,
    RoleChangeIn,
)
from app.core.config import Settings
from app.core.errors import ValidationError
from app.db.session import Databases
from app.domain.enums import UserRole
from app.services.auth_service import AuthService
from app.services.organisation_service import (
    MemberSummary,
    OrganisationService,
    require_admin,
)

router = APIRouter(prefix="/organisation", tags=["Organisation"])


def _member_out(member: MemberSummary) -> MemberOut:
    return MemberOut(
        id=member.id,
        email=member.email,
        full_name=member.full_name,
        role=member.role,
        role_label_fr=member.role.label_fr,
        is_active=member.is_active,
        created_at=member.created_at,
    )


@router.get("", response_model=OrganisationOut, summary="Fiche de l'organisation")
async def organisation(
    session: CurrentSession, context: CurrentContext
) -> OrganisationOut:
    profile = await OrganisationService(session, context).profile()
    return OrganisationOut(
        id=profile.id,
        name=profile.name,
        slug=profile.slug,
        region_code=profile.region_code,
        plan_code=profile.plan_code,
        is_demo=profile.is_demo,
        members=[_member_out(m) for m in profile.members],
    )


@router.post(
    "/membres",
    response_model=OrganisationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter un membre",
)
async def add_member(
    payload: MemberCreate,
    session: CurrentSession,
    context: CurrentContext,
    audit: CurrentAudit,
    settings: CurrentSettings,
    databases: Annotated[Databases, Depends(get_databases)],
) -> OrganisationOut:
    """Crée un compte dans l'organisation, avec son rôle.

    **Aucun courriel n'est envoyé** : la plateforme n'a pas d'acheminement de
    courrier. L'administrateur choisit un mot de passe provisoire et le
    transmet par un autre canal. Annoncer « invitation envoyée » sans rien
    envoyer serait la pire des deux options.
    """
    require_admin(context, "ajouter un membre")
    service = OrganisationService(session, context)
    existing = {member.email for member in await service.members()}
    if payload.email.lower() in existing:
        raise ValidationError(
            f"Un membre utilise déjà le courriel « {payload.email} ».",
            remedy_fr="Utilisez une autre adresse, ou modifiez le membre existant.",
        )

    auth = AuthService(
        databases, jwt_secret=settings.jwt_secret, ttl_minutes=settings.jwt_ttl_minutes
    )
    user_id = await auth.provision_user(
        tenant_id=context.tenant_id,
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password,
        role=UserRole(payload.role),
    )
    await audit.record(
        context,
        action="member:create",
        resource_type="USER",
        resource_id=str(user_id),
        outcome="SUCCESS",
        detail={"role": payload.role.value},
    )
    return await organisation(session, context)


@router.post(
    "/membres/{user_id}/role",
    response_model=OrganisationOut,
    summary="Changer le rôle d'un membre",
)
async def change_role(
    user_id: uuid.UUID,
    payload: RoleChangeIn,
    session: CurrentSession,
    context: CurrentContext,
    audit: CurrentAudit,
) -> OrganisationOut:
    require_admin(context, "changer le rôle d'un membre")
    service = OrganisationService(session, context)
    await service.set_role(user_id, payload.role)
    await audit.record(
        context,
        action="member:set_role",
        resource_type="USER",
        resource_id=str(user_id),
        outcome="SUCCESS",
        detail={"role": payload.role.value},
    )
    return await organisation(session, context)


@router.get(
    "/journal",
    response_model=list[AuditEntryOut],
    summary="Journal des accès et des décisions",
)
async def audit_journal(
    session: CurrentSession,
    context: CurrentContext,
    audit: CurrentAudit,
    limit: int = 100,
) -> list[AuditEntryOut]:
    """Qui a vu quoi, qui a approuvé quoi, qui a changé quel seuil (§9).

    La consultation elle-même est inscrite : c'est ce qui distingue un journal
    d'audit d'une simple liste d'évènements.
    """
    require_admin(context, "consulter le journal d'audit")
    entries = await OrganisationService(session, context).audit_entries(limit=limit)
    await audit.record(
        context,
        action="audit:read",
        resource_type="AUDIT_LOG",
        outcome="SUCCESS",
        detail={"returned": len(entries)},
    )
    return [
        AuditEntryOut(
            occurred_at=entry.occurred_at,
            actor_email=entry.actor_email,
            action=entry.action,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            outcome=entry.outcome,
            run_id=entry.run_id,
            detail=entry.detail,
        )
        for entry in entries
    ]


@router.get("/export", summary="Exporter toutes les données de l'organisation")
async def export_data(
    session: CurrentSession, context: CurrentContext, audit: CurrentAudit
) -> dict[str, object]:
    """Portabilité : une copie exploitable, table par table.

    Pas de `response_model` : la forme de l'export suit le modèle de données et
    change avec lui. Un schéma figé ici deviendrait faux au premier ajout de
    table, et un export qui se croit complet est pire qu'un export absent.
    """
    require_admin(context, "exporter les données de l'organisation")
    payload = await OrganisationService(session, context).export()
    await audit.record(
        context,
        action="organisation:export",
        resource_type="TENANT",
        resource_id=str(context.tenant_id),
        outcome="SUCCESS",
        detail={"tables": len(payload["tables"])},
    )
    return payload


@router.delete("/membres/{user_id}", summary="Supprimer un membre")
async def delete_member(
    user_id: uuid.UUID,
    session: CurrentSession,
    context: CurrentContext,
    audit: CurrentAudit,
) -> dict[str, str]:
    """Droit à la suppression, pour une personne.

    Le journal subsiste, pseudonymisé : il est la preuve des accès subis par
    les autres membres, et l'effacer au nom du droit de l'un retirerait aux
    autres le leur. `docs/conformite.md` le dit en ces termes.
    """
    require_admin(context, "supprimer un membre")
    email = await OrganisationService(session, context).delete_member(user_id)
    await audit.record(
        context,
        action="member:delete",
        resource_type="USER",
        resource_id=str(user_id),
        outcome="SUCCESS",
        # Le courriel figure ici parce que la trace de la suppression doit dire
        # *qui* a été supprimé ; c'est la dernière ligne où il apparaît.
        detail={"email": email},
    )
    return {
        "message_fr": (
            f"Le compte « {email} » a été supprimé. Ses actions restent au "
            "journal d'audit sous un pseudonyme."
        )
    }


@router.delete(
    "",
    response_model=DeletionReceiptOut,
    summary="Supprimer l'organisation et toutes ses données",
)
async def delete_organisation(
    payload: DeletionRequestIn,
    session: CurrentSession,
    context: CurrentContext,
    audit: CurrentAudit,
) -> DeletionReceiptOut:
    """Effacement complet. Irréversible, et confirmé en recopiant l'identifiant.

    L'audit est écrit **avant** la suppression : écrit après, il porterait sur
    une organisation dont la ligne n'existe plus, et la trace de l'acte
    disparaîtrait avec son objet.
    """
    require_admin(context, "supprimer l'organisation")
    await audit.record(
        context,
        action="organisation:delete",
        resource_type="TENANT",
        resource_id=str(context.tenant_id),
        outcome="REQUESTED",
    )
    deleted = await OrganisationService(session, context).delete_organisation(
        payload.confirmation
    )
    total = sum(deleted.values())
    return DeletionReceiptOut(
        organisation_id=context.tenant_id,
        deleted_rows=deleted,
        total_rows=total,
        message_fr=(
            f"{total} enregistrements ont été supprimés définitivement. "
            "Aucune sauvegarde n'est conservée par la plateforme."
        ),
    )


@router.get(
    "/conformite",
    response_model=ComplianceOut,
    summary="Résidence des données, conservation et limites",
)
async def compliance(settings: CurrentSettings, _: CurrentContext) -> ComplianceOut:
    """Ce que l'exploitant a déclaré, et ce que la plateforme ne garantit pas.

    Les déclarations viennent de la configuration, pas d'une constante : le code
    ne peut pas savoir où tourne sa base. Non renseignées, elles rendent `null`
    et l'écran affiche « non déclarée » — jamais « Maroc » par défaut, qui
    serait une affirmation de conformité que personne n'a vérifiée.
    """
    return ComplianceOut(
        residency_country=settings.data_residency_country,
        residency_provider=settings.data_residency_provider,
        cndp_declaration_number=settings.cndp_declaration_number,
        audit_retention_days=settings.audit_retention_days,
        # Faux tant qu'aucune tâche ne purge : annoncer une durée qu'on
        # n'applique pas serait exactement le genre de conformité de façade que
        # ce produit refuse.
        retention_enforced=False,
        statements_fr=_statements(settings),
        limitations_fr=[
            "La durée de conservation est annoncée mais n'est appliquée par "
            "aucune purge automatique : les lignes échues restent en base.",
            "La suppression d'un membre pseudonymise ses traces au journal "
            "d'audit sans les effacer : le journal est la preuve des accès "
            "subis par les autres membres.",
            "Aucun chiffrement au repos n'est assuré par l'application : il "
            "relève de l'hébergeur et n'a pas été vérifié depuis le code.",
            "Aucun registre des sous-traitants n'est tenu par la plateforme.",
        ],
    )


def _statements(settings: Settings) -> list[str]:
    if settings.data_residency_country:
        host = settings.data_residency_provider
        residency = f"Les données sont hébergées en {settings.data_residency_country}" + (
            f" chez {host}." if host else "."
        )
    else:
        residency = (
            "La localisation des données n'est pas déclarée par cette installation."
        )
    declaration = (
        f"Déclaration CNDP n° {settings.cndp_declaration_number}."
        if settings.cndp_declaration_number
        else "Aucune déclaration CNDP n'est enregistrée pour cette installation."
    )
    return [
        residency,
        declaration,
        "Chaque organisation ne voit que ses propres données, garanti par une "
        "politique de sécurité au niveau des lignes dans la base, et non par le "
        "code applicatif.",
        "Toute consultation du journal d'audit, tout export et toute "
        "suppression sont eux-mêmes inscrits au journal.",
        "Vous pouvez à tout moment exporter l'intégralité de vos données ou en "
        "demander la suppression définitive depuis cette console.",
    ]
