"""Premier parcours : créer une parcelle, saisir un relevé, obtenir un avis.

Le but tenu par ce module est une durée : **moins de dix minutes** entre la
création d'un compte et la première recommandation lisible. Ce qui coûte ces
minutes n'est pas la saisie, c'est de deviner — quel code de culture existe,
pourquoi la recommandation est bloquée, ce qu'il reste à faire. Le service rend
donc l'état du parcours plutôt que de laisser l'interface le déduire, et chaque
refus nomme les valeurs acceptables.

Deux règles s'y appliquent sans exception :

* la **provenance** d'un relevé saisi au clavier est décidée ici, jamais reçue.
  Un client ne peut pas déclarer qu'une saisie manuelle vient d'une sonde ;
* un champ absent le reste. Pas de débit par défaut, pas de date de plantation
  supposée : le moteur dira « indisponible », et c'est la bonne réponse.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.core.security import RequestContext
from app.db.base import (
    Crop,
    Field,
    IrrigationSystem,
    Site,
    SoilMoistureReading,
    SoilProfile,
)
from app.domain.enums import DataOrigin, DataState, SiteType
from app.domain.provenance import SOURCE_MANUAL_ENTRY

__all__ = ["OnboardingService", "OnboardingStep", "ReferenceChoice"]

#: Les trois référentiels que le formulaire propose. Une variable de type à
#: valeurs restreintes plutôt qu'une union : mypy vérifie alors chaque cas
#: séparément et `_resolve` rend la classe demandée, pas une base commune.
_Reference = TypeVar("_Reference", Crop, SoilProfile, IrrigationSystem)


@dataclass(frozen=True, slots=True)
class ReferenceChoice:
    """Une valeur proposée dans le formulaire, et d'où elle vient.

    `is_local` distingue une ligne de référence globale d'une calibration posée
    par l'organisation : deux valeurs de même nom qui n'engagent pas la même
    chose.
    """

    code: str
    name_fr: str
    is_local: bool


@dataclass(frozen=True, slots=True)
class OnboardingStep:
    """Une étape du parcours, avec de quoi la franchir."""

    key: str
    title_fr: str
    detail_fr: str
    done: bool
    #: Ce qu'il faut faire maintenant. `None` quand l'étape est franchie.
    action_fr: str | None


@dataclass(frozen=True, slots=True)
class OnboardingState:
    steps: list[OnboardingStep]
    #: Code de la première parcelle, quand il en existe une : l'interface a
    #: besoin de savoir *où* emmener l'utilisateur, pas seulement qu'il reste
    #: une étape.
    first_field_code: str | None

    @property
    def complete(self) -> bool:
        return all(step.done for step in self.steps)


class OnboardingService:
    def __init__(self, session: AsyncSession, context: RequestContext) -> None:
        self._session = session
        self._context = context

    # -- ce que le formulaire propose ---------------------------------------

    async def choices(self) -> dict[str, list[ReferenceChoice]]:
        """Cultures, sols et systèmes disponibles pour cette organisation.

        Une liste plutôt qu'un champ libre : « tomate », « Tomate » et
        « tomates » désigneraient trois cultures inexistantes, et la première
        recommandation échouerait sur une faute de frappe.
        """
        return {
            "crops": await self._reference(Crop),
            "soils": await self._reference(SoilProfile),
            "irrigation_systems": await self._reference(IrrigationSystem),
            "sites": [
                ReferenceChoice(code=site.code, name_fr=site.name_fr, is_local=True)
                for site in (
                    await self._session.execute(
                        select(Site)
                        .where(Site.site_type == SiteType.FARM)
                        .order_by(Site.name_fr)
                    )
                ).scalars()
            ],
        }

    async def _reference(self, model: type[_Reference]) -> list[ReferenceChoice]:
        rows = list(
            (
                await self._session.execute(select(model).order_by(model.name_fr))
            ).scalars()
        )
        # Une ligne locale masque la ligne globale de même code (voir
        # `ReferenceScoped`). Les deux apparaîtraient sinon dans la liste, sous
        # le même nom, sans qu'on puisse les distinguer.
        by_code: dict[str, ReferenceChoice] = {}
        for row in rows:
            local = row.tenant_id is not None
            if row.code in by_code and not local:
                continue
            by_code[row.code] = ReferenceChoice(
                code=row.code, name_fr=row.name_fr, is_local=local
            )
        return sorted(by_code.values(), key=lambda choice: choice.name_fr)

    # -- création ------------------------------------------------------------

    async def create_field(
        self,
        *,
        code: str,
        name_fr: str,
        site_code: str,
        area_ha: float,
        latitude: float,
        longitude: float,
        crop_code: str,
        soil_code: str,
        irrigation_system_code: str,
        planting_date: date | None = None,
        flow_rate_m3_per_hour: float | None = None,
        water_cost_per_m3: float | None = None,
        boundary_geojson: dict[str, Any] | None = None,
    ) -> Field:
        site = await self._site(site_code)
        crop = await self._resolve(Crop, crop_code, "culture")
        soil = await self._resolve(SoilProfile, soil_code, "sol")
        system = await self._resolve(
            IrrigationSystem, irrigation_system_code, "système d'irrigation"
        )

        normalised = code.strip().upper()
        existing = (
            await self._session.execute(
                select(func.count()).select_from(Field).where(Field.code == normalised)
            )
        ).scalar_one()
        if existing:
            raise ValidationError(
                f"Une parcelle porte déjà le code « {normalised} ».",
                remedy_fr="Choisissez un autre code, ou modifiez la parcelle existante.",
            )

        field = Field(
            id=uuid.uuid4(),
            tenant_id=self._context.tenant_id,
            site_id=site.id,
            code=normalised,
            name_fr=name_fr.strip(),
            area_ha=area_ha,
            latitude=latitude,
            longitude=longitude,
            boundary_geojson=boundary_geojson,
            crop_id=crop.id,
            soil_profile_id=soil.id,
            irrigation_system_id=system.id,
            planting_date=planting_date,
            # Restent nuls quand ils ne sont pas fournis. Un débit par défaut
            # produirait une durée d'arrosage fausse et crédible ; un tarif par
            # défaut produirait un coût faux et crédible.
            flow_rate_m3_per_hour=flow_rate_m3_per_hour,
            water_cost_per_m3=water_cost_per_m3,
        )
        self._session.add(field)
        await self._session.flush()
        return field

    async def record_moisture(
        self,
        *,
        field_code: str,
        value_pct: float,
        depth_cm: float | None = None,
        recorded_at: datetime | None = None,
        note_fr: str | None = None,
    ) -> SoilMoistureReading:
        """Enregistre un relevé **saisi à la main**, et le dit dans le schéma.

        Le couple provenance est écrit par le serveur : `OBSERVED` parce que
        quelqu'un a constaté la valeur, `MANUAL_ENTRY` parce que personne n'a
        constaté l'instrument. L'interface affichera « saisie manuelle », et un
        futur relevé de sonde ne se confondra pas avec celui-ci.
        """
        field = await self._field(field_code)
        moment = recorded_at or datetime.now(UTC)
        if moment > datetime.now(UTC):
            raise ValidationError(
                "Un relevé ne peut pas être daté dans le futur.",
                remedy_fr="Corrigez la date, ou laissez-la vide pour l'instant présent.",
            )
        reading = SoilMoistureReading(
            id=uuid.uuid4(),
            tenant_id=self._context.tenant_id,
            field_id=field.id,
            value_pct=value_pct,
            depth_cm=depth_cm,
            recorded_at=moment,
            sensor_id=None,
            note_fr=note_fr,
            data_state=DataState.OBSERVED,
            data_origin=DataOrigin.MANUAL_ENTRY,
            source_id=SOURCE_MANUAL_ENTRY.id,
        )
        self._session.add(reading)
        await self._session.flush()
        return reading

    # -- où en est-on --------------------------------------------------------

    async def state(self) -> OnboardingState:
        """Les étapes, dans l'ordre, avec ce qui reste à faire.

        Rendu par le serveur pour la même raison que les capacités non livrées
        le sont : le jour où une étape disparaît, l'écran cesse de la demander
        sans qu'on le modifie.
        """
        site_count = await self._count(Site)
        field = (
            await self._session.execute(
                select(Field).order_by(Field.created_at).limit(1)
            )
        ).scalar_one_or_none()
        reading_count = 0
        if field is not None:
            reading_count = int(
                (
                    await self._session.execute(
                        select(func.count())
                        .select_from(SoilMoistureReading)
                        .where(SoilMoistureReading.field_id == field.id)
                    )
                ).scalar_one()
            )

        ready = field is not None and reading_count > 0
        steps = [
            OnboardingStep(
                key="site",
                title_fr="Déclarer une exploitation",
                detail_fr="Le lieu auquel vos parcelles se rattachent.",
                done=site_count > 0,
                action_fr=None if site_count else "Créez votre premier site.",
            ),
            OnboardingStep(
                key="field",
                title_fr="Créer une première parcelle",
                detail_fr="Surface, position, culture, sol et système d'irrigation.",
                done=field is not None,
                action_fr=None if field is not None else "Ajoutez une parcelle.",
            ),
            OnboardingStep(
                key="moisture",
                title_fr="Saisir un relevé d'humidité",
                detail_fr=(
                    "Une mesure au tensiomètre ou à la sonde portative suffit. "
                    "Sans elle, le bilan hydrique part d'une hypothèse au lieu "
                    "d'une mesure."
                ),
                done=reading_count > 0,
                action_fr=None if reading_count else "Relevez l'humidité de la parcelle.",
            ),
            OnboardingStep(
                key="recommendation",
                title_fr="Obtenir un premier avis d'irrigation",
                detail_fr=(
                    "Calculé par le moteur FAO-56, avec ses entrées et leur "
                    "provenance."
                ),
                done=ready,
                action_fr=None if ready else "Ouvrez la fiche de la parcelle.",
            ),
        ]
        return OnboardingState(
            steps=steps, first_field_code=field.code if field is not None else None
        )

    # -- résolution ----------------------------------------------------------

    async def _site(self, code: str) -> Site:
        site = (
            await self._session.execute(
                select(Site).where(Site.code == code.strip().upper())
            )
        ).scalar_one_or_none()
        if site is None:
            known = [
                row.code
                for row in (
                    await self._session.execute(
                        select(Site).where(Site.site_type == SiteType.FARM)
                    )
                ).scalars()
            ]
            raise ValidationError(
                f"Le site « {code} » n'existe pas.",
                remedy_fr=(
                    f"Sites connus : {', '.join(sorted(known))}."
                    if known
                    else "Créez d'abord un site d'exploitation."
                ),
            )
        return site

    async def _field(self, code: str) -> Field:
        field = (
            await self._session.execute(
                select(Field).where(Field.code == code.strip().upper())
            )
        ).scalar_one_or_none()
        if field is None:
            raise ValidationError(
                f"La parcelle « {code} » n'existe pas.",
                remedy_fr="Vérifiez le code de la parcelle.",
            )
        return field

    async def _resolve(
        self, model: type[_Reference], code: str, label_fr: str
    ) -> _Reference:
        """Résout un code de référentiel, la ligne locale primant sur la globale.

        L'échec nomme les codes acceptés. « Culture inconnue » oblige à
        deviner ; « cultures connues : TOMATE, AGRUME… » se corrige en une fois.
        """
        wanted = code.strip().upper()
        rows = list(
            (
                await self._session.execute(select(model).where(model.code == wanted))
            ).scalars()
        )
        for row in rows:
            if row.tenant_id is not None:
                return row
        if rows:
            return rows[0]

        known = sorted(choice.code for choice in await self._reference(model))
        raise ValidationError(
            f"La {label_fr} « {code} » n'existe pas au catalogue.",
            remedy_fr=(
                f"Valeurs connues : {', '.join(known)}."
                if known
                else f"Aucune {label_fr} n'est encore au catalogue."
            ),
        )

    async def _count(self, model: type[Site]) -> int:
        return int(
            (
                await self._session.execute(select(func.count()).select_from(model))
            ).scalar_one()
        )
