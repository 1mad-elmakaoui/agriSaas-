"""Premier parcours : de l'inscription au premier avis d'irrigation.

Trois routes seulement, parce que le parcours n'a que trois actes : savoir ce
qu'on peut choisir, créer une parcelle, saisir un relevé. La quatrième étape —
l'avis — existe déjà sur la fiche de la parcelle ; l'inventer ici en créerait une
seconde version, et deux versions d'un même chiffre finissent par diverger.

Les deux créations passent par le plafond de stock **avant** d'écrire. Un quota
vérifié après la création aurait laissé la parcelle en base, et il aurait fallu
la supprimer — c'est-à-dire décider à la place de l'exploitant.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentContext, CurrentSession
from app.api.schemas import (
    OnboardingChoicesOut,
    OnboardingStateOut,
    OnboardingStepOut,
    ReferenceChoiceOut,
)
from app.services.onboarding_service import OnboardingService, ReferenceChoice

router = APIRouter(prefix="/demarrage", tags=["Démarrage"])


def _choices(items: list[ReferenceChoice]) -> list[ReferenceChoiceOut]:
    return [
        ReferenceChoiceOut(code=c.code, name_fr=c.name_fr, is_local=c.is_local)
        for c in items
    ]


@router.get("", response_model=OnboardingStateOut, summary="Où en est le démarrage")
async def onboarding_state(
    session: CurrentSession, context: CurrentContext
) -> OnboardingStateOut:
    state = await OnboardingService(session, context).state()
    return OnboardingStateOut(
        steps=[
            OnboardingStepOut(
                key=step.key,
                title_fr=step.title_fr,
                detail_fr=step.detail_fr,
                done=step.done,
                action_fr=step.action_fr,
            )
            for step in state.steps
        ],
        complete=state.complete,
        first_field_code=state.first_field_code,
    )


@router.get(
    "/choix",
    response_model=OnboardingChoicesOut,
    summary="Valeurs proposées par le catalogue",
)
async def onboarding_choices(
    session: CurrentSession, context: CurrentContext
) -> OnboardingChoicesOut:
    choices = await OnboardingService(session, context).choices()
    return OnboardingChoicesOut(
        sites=_choices(choices["sites"]),
        crops=_choices(choices["crops"]),
        soils=_choices(choices["soils"]),
        irrigation_systems=_choices(choices["irrigation_systems"]),
    )
