"""Recommandations : la trace des décisions, et le verdict humain.

Deux opérations, et leur séparation est le point : le moteur propose, une
personne dispose. Rien ici n'exécute une irrigation ni ne déplace une
expédition — le verdict consigne ce qu'un responsable a décidé, il ne le fait
pas à sa place.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import CurrentAudit, CurrentContext, CurrentSession
from app.api.schemas import (
    ProvenanceOut,
    RecommendationCountsOut,
    RecommendationIn,
    RecommendationOut,
    VerdictIn,
)
from app.domain.decision import HumanVerdict
from app.services.recommendation_service import (
    RecommendationService,
    RecommendationSummary,
)

router = APIRouter(prefix="/recommandations", tags=["Recommandations"])


def _to_out(summary: RecommendationSummary) -> RecommendationOut:
    return RecommendationOut(
        id=summary.id,
        domain=summary.domain.value,
        domain_label_fr=summary.domain.label_fr,
        subject_id=summary.subject_id,
        subject_label_fr=summary.subject_label_fr,
        headline_fr=summary.headline_fr,
        outcome_code=summary.outcome_code,
        rationale_fr=summary.rationale_fr,
        verdict=summary.verdict.value,
        verdict_label_fr=summary.verdict.label_fr,
        decided_at=summary.decided_at,
        decided_by_name=summary.decided_by_name,
        decision_note_fr=summary.decision_note_fr,
        created_at=summary.created_at,
        provenance=ProvenanceOut(
            state=summary.data_state,
            state_label_fr=summary.data_state.label_fr,
            origin=summary.data_origin,
            origin_label_fr=summary.data_origin.label_fr,
            source_id="decision-engine",
            source_label_fr="Moteur de décision",
        ),
    )


@router.get("", response_model=list[RecommendationOut], summary="Lister les recommandations")
async def list_recommendations(
    session: CurrentSession, context: CurrentContext
) -> list[RecommendationOut]:
    service = RecommendationService(session, context)
    return [_to_out(s) for s in await service.list_recent()]


@router.post(
    "",
    response_model=RecommendationOut,
    status_code=201,
    summary="Enregistrer une proposition",
)
async def record(
    payload: RecommendationIn,
    session: CurrentSession,
    context: CurrentContext,
    audit: CurrentAudit,
) -> RecommendationOut:
    """Recalcule la décision, puis l'archive **en attente**.

    Le client envoie un sujet, pas une décision. Accepter une décision du
    navigateur laisserait archiver n'importe quel chiffre sous le nom du moteur,
    et la question « qu'avions-nous conseillé ? » deviendrait sans valeur.
    """
    from app.adapters.weather import build_weather_provider
    from app.core.config import get_settings
    from app.db.base import Tenant
    from app.domain.decision import DecisionDomain
    from app.domain.enums import ORIGIN_BY_RELIABILITY, DataOrigin, DataState
    from app.repositories.tenant import TenantRepository
    from app.services.irrigation_service import IrrigationService
    from app.services.logistics_service import LogisticsService

    tenant: Tenant = await TenantRepository(session, context).current_tenant()
    provider = build_weather_provider(
        "offline" if tenant.is_demo or get_settings().demo_mode else "open-meteo"
    )

    domain = DecisionDomain(payload.domain)
    if domain is DecisionDomain.IRRIGATION:
        decision = (
            await IrrigationService(session, provider).decide(payload.subject_id)
        ).decision
    else:
        decision = (
            await LogisticsService(session, provider).analyse(payload.subject_id)
        ).decision

    origins = [i.origin for i in decision.inputs if i.value is not None]
    weakest = (
        max(origins, key=ORIGIN_BY_RELIABILITY.index) if origins else DataOrigin.MODEL
    )

    service = RecommendationService(session, context)
    row = await service.record(
        decision,
        rationale_fr=payload.rationale_fr,
        data_state=DataState.DERIVED,
        data_origin=weakest,
    )
    await audit.record(
        context,
        action="recommendation:create",
        resource_type="RECOMMENDATION",
        resource_id=str(row.id),
        outcome="SUCCESS",
        detail={"domain": payload.domain, "subject": payload.subject_id},
    )

    summaries = {s.id: s for s in await service.list_recent()}
    return _to_out(summaries[row.id])


@router.get(
    "/decompte",
    response_model=RecommendationCountsOut,
    summary="Décompte des verdicts",
)
async def counts(
    session: CurrentSession, context: CurrentContext
) -> RecommendationCountsOut:
    """Ce que la question de clôture de la §12 demande.

    Un décompte, pas un score : chaque nombre se retrouve à la main depuis la
    liste.
    """
    tally = await RecommendationService(session, context).counts()
    rate = tally.acceptance_rate
    return RecommendationCountsOut(
        pending=tally.pending,
        accepted=tally.accepted,
        rejected=tally.rejected,
        modified=tally.modified,
        decided=tally.decided,
        total=tally.total,
        acceptance_rate=rate,
        acceptance_label_fr=(
            f"{rate:.0%} des décisions rendues"
            if rate is not None
            else "Aucune décision rendue pour l'instant."
        ),
    )


@router.post(
    "/{recommendation_id}/verdict",
    response_model=RecommendationOut,
    summary="Rendre un verdict",
)
async def decide(
    recommendation_id: uuid.UUID,
    payload: VerdictIn,
    session: CurrentSession,
    context: CurrentContext,
    audit: CurrentAudit,
) -> RecommendationOut:
    service = RecommendationService(session, context)
    row = await service.decide(
        recommendation_id,
        verdict=HumanVerdict(payload.verdict),
        note_fr=payload.note_fr,
    )

    # Qui a approuvé quoi : c'est la ligne que §9 demande de journaliser.
    await audit.record(
        context,
        action="recommendation:decide",
        resource_type="RECOMMENDATION",
        resource_id=str(recommendation_id),
        outcome="SUCCESS",
        detail={"verdict": payload.verdict, "subject": row.subject_id},
    )

    summaries = {s.id: s for s in await service.list_recent()}
    return _to_out(summaries[row.id])
