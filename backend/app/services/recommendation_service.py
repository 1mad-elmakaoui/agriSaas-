"""Enregistrement et suivi des recommandations.

Ce service porte la seule écriture métier du produit, et la règle qui la
gouverne : **aucun outil n'exécute d'action irréversible.** Enregistrer une
proposition n'est pas l'appliquer. Le verdict est une seconde opération,
délibérée, faite par une personne identifiée.

Deux invariants tenus ici plutôt que par convention :

1. **Un verdict ne se rend qu'une fois.** Rendre un verdict sur une
   recommandation déjà décidée écraserait la trace de qui avait décidé quoi —
   exactement ce que la table existe pour conserver. Une décision changée est
   une nouvelle recommandation, pas une réécriture.
2. **La décision est figée au moment de l'enregistrement.** Le `payload` est
   copié, pas référencé : recalculer six mois plus tard donnerait un autre
   chiffre, sur d'autres données.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import RequestContext
from app.db.base import Recommendation, User
from app.domain.decision import Decision, DecisionDomain, HumanVerdict
from app.domain.enums import DataOrigin, DataState

logger = get_logger(__name__)

__all__ = ["RecommendationService", "RecommendationSummary", "VerdictCounts"]

SOURCE_DECISION_ENGINE = "decision-engine"


@dataclass(frozen=True, slots=True)
class RecommendationSummary:
    id: uuid.UUID
    domain: DecisionDomain
    subject_id: str
    subject_label_fr: str
    headline_fr: str
    outcome_code: str
    rationale_fr: str | None
    verdict: HumanVerdict
    decided_at: datetime | None
    decided_by_name: str | None
    decision_note_fr: str | None
    created_at: datetime
    data_state: DataState
    data_origin: DataOrigin


@dataclass(frozen=True, slots=True)
class VerdictCounts:
    """Le décompte que la question de clôture de la §12 demande."""

    pending: int
    accepted: int
    rejected: int
    modified: int

    @property
    def decided(self) -> int:
        return self.accepted + self.rejected + self.modified

    @property
    def total(self) -> int:
        return self.pending + self.decided

    @property
    def acceptance_rate(self) -> float | None:
        """`None` tant que rien n'a été décidé.

        Un taux de 0 % sur zéro décision se lirait comme « tout est refusé ».
        Ce qui manque manque.
        """
        return round(self.accepted / self.decided, 3) if self.decided else None


class RecommendationService:
    def __init__(self, session: AsyncSession, context: RequestContext) -> None:
        self._session = session
        self._context = context

    async def record(
        self,
        decision: Decision,
        *,
        rationale_fr: str | None = None,
        data_state: DataState,
        data_origin: DataOrigin,
    ) -> Recommendation:
        """Enregistre une proposition **en attente**.

        La provenance est passée par l'appelant, jamais déduite : une
        recommandation calculée sur un jeu de démonstration et une
        recommandation calculée sur des mesures réelles se ressemblent trait
        pour trait, et seule cette paire les distingue.
        """
        row = Recommendation(
            id=uuid.uuid4(),
            tenant_id=self._context.tenant_id,
            domain=decision.domain,
            subject_id=decision.subject_id,
            subject_label_fr=decision.subject_label_fr,
            headline_fr=decision.headline_fr,
            outcome_code=decision.outcome_code,
            rationale_fr=rationale_fr,
            payload=decision.to_dict(),
            verdict=HumanVerdict.PENDING,
            created_by=self._context.user_id,
            data_state=data_state,
            data_origin=data_origin,
            source_id=SOURCE_DECISION_ENGINE,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info(
            "recommendation_recorded",
            domain=decision.domain.value,
            subject=decision.subject_id,
            outcome=decision.outcome_code,
        )
        return row

    async def decide(
        self,
        recommendation_id: uuid.UUID,
        *,
        verdict: HumanVerdict,
        note_fr: str | None = None,
    ) -> Recommendation:
        """Rend un verdict, une seule fois.

        Écraser un verdict existant effacerait qui avait décidé quoi — ce que
        la table existe précisément pour conserver. Une décision qui change est
        une nouvelle recommandation.
        """
        if verdict is HumanVerdict.PENDING:
            raise ValidationError(
                "« En attente » n'est pas un verdict : c'est l'état initial.",
                remedy_fr="Choisissez Acceptée, Refusée, ou Appliquée avec modification.",
            )

        row = (
            await self._session.execute(
                select(Recommendation).where(Recommendation.id == recommendation_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError(
                "Recommandation introuvable.",
                remedy_fr="Elle a pu être supprimée, ou appartenir à une autre organisation.",
            )
        if row.verdict is not HumanVerdict.PENDING:
            raise ValidationError(
                f"Cette recommandation a déjà été traitée : "
                f"{row.verdict.label_fr.lower()}.",
                remedy_fr=(
                    "Une décision qui change se consigne comme une nouvelle "
                    "recommandation, pour que la trace de la première subsiste."
                ),
            )

        row.verdict = verdict
        row.decided_at = datetime.now(UTC)
        row.decided_by = self._context.user_id
        row.decision_note_fr = note_fr
        await self._session.flush()
        logger.info(
            "recommendation_decided",
            recommendation=str(recommendation_id),
            verdict=verdict.value,
        )
        return row

    async def list_recent(self, *, limit: int = 50) -> list[RecommendationSummary]:
        rows = list(
            (
                await self._session.execute(
                    select(Recommendation)
                    .order_by(Recommendation.created_at.desc())
                    .limit(limit)
                )
            ).scalars()
        )
        names = await self._decider_names(rows)
        return [
            RecommendationSummary(
                id=row.id,
                domain=row.domain,
                subject_id=row.subject_id,
                subject_label_fr=row.subject_label_fr,
                headline_fr=row.headline_fr,
                outcome_code=row.outcome_code,
                rationale_fr=row.rationale_fr,
                verdict=row.verdict,
                decided_at=row.decided_at,
                decided_by_name=names.get(row.decided_by),
                decision_note_fr=row.decision_note_fr,
                created_at=row.created_at,
                data_state=row.data_state,
                data_origin=row.data_origin,
            )
            for row in rows
        ]

    async def counts(self) -> VerdictCounts:
        rows = (
            await self._session.execute(
                select(Recommendation.verdict, func.count())
                .group_by(Recommendation.verdict)
            )
        ).all()
        by_verdict = {verdict: count for verdict, count in rows}
        return VerdictCounts(
            pending=by_verdict.get(HumanVerdict.PENDING, 0),
            accepted=by_verdict.get(HumanVerdict.ACCEPTED, 0),
            rejected=by_verdict.get(HumanVerdict.REJECTED, 0),
            modified=by_verdict.get(HumanVerdict.MODIFIED, 0),
        )

    async def _decider_names(
        self, rows: list[Recommendation]
    ) -> dict[uuid.UUID | None, str]:
        ids = {row.decided_by for row in rows if row.decided_by is not None}
        if not ids:
            return {}
        users = (
            await self._session.execute(select(User).where(User.id.in_(ids)))
        ).scalars()
        return {user.id: user.full_name for user in users}
