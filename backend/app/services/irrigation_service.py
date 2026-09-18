"""Service d'irrigation : résout le contexte, appelle le moteur, rend une décision.

La séparation est stricte et c'est elle qui tient la doctrine. **Ce module fait
les entrées/sorties ; il ne calcule rien.** Il résout une parcelle en culture,
sol, système, stade, humidité et météo, passe le tout au moteur pur de
`app.domain.irrigation`, puis met en forme le résultat dans le contrat commun
`Decision`.

Aucun nombre affiché ne naît ici : chacun vient d'une fonction du domaine, et
chaque entrée arrive avec son couple de provenance jusqu'au panneau
« Pourquoi cette décision ? ».
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.weather import DailyWeather, WeatherProvider
from app.core.errors import NotFoundError, ValidationError
from app.db.base import (
    Crop,
    CropGrowthStage,
    Field,
    IrrigationSystem,
    SoilMoistureReading,
    SoilProfile,
)
from app.domain.decision import (
    Decision,
    DecisionDomain,
    DecisionInput,
    EvidenceItem,
)
from app.domain.enums import DataOrigin, DataState, GrowthStage, StageSource
from app.domain.formatting import fr, fr_pct
from app.domain.irrigation.constants import Recommendation
from app.domain.irrigation.crop_water import (
    CropParameters,
    CropWaterRequirement,
    GrowthStageInfo,
    calculate_crop_evapotranspiration,
    estimate_growth_stage,
)
from app.domain.irrigation.et0 import ET0Result, calculate_et0
from app.domain.irrigation.explain import DataQuality
from app.domain.irrigation.irrigation import (
    IrrigationDuration,
    IrrigationRequirement,
    IrrigationSystemParameters,
    WaterCost,
    calculate_irrigation_duration,
    calculate_irrigation_requirement,
    calculate_water_cost,
    format_duration_fr,
)
from app.domain.irrigation.water_balance import (
    SoilParameters,
    WaterBalanceResult,
    calculate_water_balance,
)
from app.domain.provenance import DataSourceRef, reliability_level

__all__ = ["IrrigationOutcome", "IrrigationService"]

SOURCE_FAO = DataSourceRef(
    id="fao-reference", label_fr="Référentiel FAO", kind="reference"
)
SOURCE_ENGINE = DataSourceRef(
    id="irrigation-engine", label_fr="Moteur FAO-56", kind="model"
)


@dataclass(frozen=True, slots=True)
class IrrigationOutcome:
    """Décision + les objets bruts, pour les outils qui veulent le détail."""

    decision: Decision
    volume_m3: float
    net_requirement_mm: float
    duration_minutes: float | None
    estimated_cost_mad: float | None
    et0_mm_day: float
    etc_mm_day: float
    stress_level: str
    recommendation: Recommendation


class IrrigationService:
    def __init__(self, session: AsyncSession, weather: WeatherProvider) -> None:
        self._session = session
        self._weather = weather

    # -- résolution du contexte -------------------------------------------

    async def _field_by_code(self, code: str) -> Field:
        field = (
            await self._session.execute(select(Field).where(Field.code == code.upper()))
        ).scalar_one_or_none()
        if field is None:
            raise NotFoundError(
                f"Parcelle « {code} » introuvable.",
                remedy_fr="Vérifiez le code de la parcelle dans la page Parcelles.",
            )
        return field

    async def _crop_parameters(self, field: Field) -> tuple[Crop, CropParameters]:
        if field.crop_id is None:
            raise ValidationError(
                f"Aucune culture n'est renseignée pour la parcelle {field.code}.",
                remedy_fr="Renseignez la culture pour obtenir une recommandation.",
            )
        crop = (
            await self._session.execute(select(Crop).where(Crop.id == field.crop_id))
        ).scalar_one()
        stages = list(
            (
                await self._session.execute(
                    select(CropGrowthStage).where(CropGrowthStage.crop_id == crop.id)
                )
            ).scalars()
        )
        lengths = {s.stage.value: s.length_days for s in stages}
        return crop, CropParameters(
            code=crop.code,
            name_fr=crop.name_fr,
            kc_initial=crop.kc_initial,
            kc_mid=crop.kc_mid,
            kc_end=crop.kc_end,
            stage_lengths_days=lengths,
            root_depth_min_m=crop.root_depth_min_m,
            root_depth_max_m=crop.root_depth_max_m,
            depletion_fraction_p=crop.depletion_fraction_p,
            perennial=crop.is_perennial,
            cycle_start_month=crop.cycle_start_month,
        )

    async def _soil(self, field: Field) -> tuple[SoilProfile, SoilParameters]:
        if field.soil_profile_id is None:
            raise ValidationError(
                f"Aucun profil de sol n'est renseigné pour la parcelle {field.code}.",
                remedy_fr="Renseignez le type de sol pour obtenir une recommandation.",
            )
        soil = (
            await self._session.execute(
                select(SoilProfile).where(SoilProfile.id == field.soil_profile_id)
            )
        ).scalar_one()
        return soil, SoilParameters(
            code=soil.code,
            name_fr=soil.name_fr,
            field_capacity=soil.theta_fc_m3_m3,
            wilting_point=soil.theta_wp_m3_m3,
            infiltration_rate_mm_per_hour=soil.infiltration_rate_mm_per_hour,
        )

    async def _system(self, field: Field) -> tuple[IrrigationSystem, IrrigationSystemParameters]:
        if field.irrigation_system_id is None:
            raise ValidationError(
                f"Aucun système d'irrigation n'est renseigné pour la parcelle "
                f"{field.code}.",
                remedy_fr="Renseignez le système d'irrigation.",
            )
        system = (
            await self._session.execute(
                select(IrrigationSystem).where(
                    IrrigationSystem.id == field.irrigation_system_id
                )
            )
        ).scalar_one()
        return system, IrrigationSystemParameters(
            code=system.code,
            name_fr=system.name_fr,
            efficiency=system.efficiency,
            flow_rate_m3_per_hour=field.flow_rate_m3_per_hour,
        )

    async def _latest_moisture(self, field: Field) -> SoilMoistureReading | None:
        return (
            await self._session.execute(
                select(SoilMoistureReading)
                .where(SoilMoistureReading.field_id == field.id)
                .order_by(SoilMoistureReading.recorded_at.desc())
                .limit(1)
            )
        ).scalars().first()

    # -- décision ----------------------------------------------------------

    async def decide(self, field_code: str) -> IrrigationOutcome:
        field = await self._field_by_code(field_code)
        crop, crop_params = await self._crop_parameters(field)
        soil, soil_params = await self._soil(field)
        system, system_params = await self._system(field)
        moisture = await self._latest_moisture(field)

        if moisture is None:
            raise ValidationError(
                f"Aucune mesure d'humidité du sol pour la parcelle {field.code}.",
                remedy_fr=(
                    "Saisissez une mesure d'humidité : sans elle, le déficit du sol "
                    "ne peut pas être calculé et aucune dose n'est proposée."
                ),
            )

        forecast = await self._weather.daily(field.latitude, field.longitude, days=7)
        if not forecast:
            raise ValidationError(
                "Aucune donnée météo disponible pour cette parcelle.",
                remedy_fr="Réessayez plus tard. Aucune valeur n'a été estimée.",
            )
        today = forecast[0]

        # --- le moteur pur, à partir d'ici -----------------------------------
        et0 = calculate_et0(
            temp_max_c=today.temp_max_c,
            temp_min_c=today.temp_min_c,
            latitude_deg=field.latitude,
            day=today.day,
            relative_humidity_max_pct=today.relative_humidity_max_pct,
            relative_humidity_min_pct=today.relative_humidity_min_pct,
            wind_speed_m_s=today.wind_speed_m_s,
            wind_measurement_height_m=today.wind_measurement_height_m,
            solar_radiation_mj_m2_day=today.solar_radiation_mj_m2_day,
            elevation_m=field.elevation_m or 0.0,
            distance_to_coast_km=field.distance_to_coast_km,
        )

        stage_info = self._stage_for(field, crop_params, today)
        crop_water = calculate_crop_evapotranspiration(
            et0_mm_day=et0.et0_mm_day, crop=crop_params, stage_info=stage_info
        )
        balance = calculate_water_balance(
            soil_moisture_pct=moisture.value_pct,
            soil=soil_params,
            root_depth_m=crop_water.root_depth_m,
            depletion_fraction_p=crop_params.depletion_fraction_p,
            etc_mm_day=crop_water.etc_mm_day,
        )
        requirement = calculate_irrigation_requirement(
            current_depletion_mm=balance.depletion_mm,
            total_available_water_mm=balance.total_available_water_mm,
            readily_available_water_mm=balance.readily_available_water_mm,
            etc_mm_day=crop_water.etc_mm_day,
            field_area_ha=field.area_ha,
            system=system_params,
            infiltration_rate_mm_per_hour=soil_params.infiltration_rate_mm_per_hour,
            seasonal_quota_remaining_m3=field.seasonal_quota_m3,
        )
        # Les deux rendent `None` quand l'entrée manque — c'est le moteur qui
        # applique « ce qui manque manque », pas ce service.
        duration = calculate_irrigation_duration(
            volume_m3=requirement.volume_m3,
            flow_rate_m3_per_hour=system_params.flow_rate_m3_per_hour,
        )
        cost = calculate_water_cost(
            volume_m3=requirement.volume_m3, cost_per_m3=field.water_cost_per_m3
        )

        decision = self._to_decision(
            field=field,
            crop=crop,
            soil=soil,
            system=system,
            moisture=moisture,
            weather=today,
            et0=et0,
            crop_water=crop_water,
            balance=balance,
            requirement=requirement,
            duration=duration,
            cost=cost,
        )
        return IrrigationOutcome(
            decision=decision,
            volume_m3=round(requirement.volume_m3, 2),
            net_requirement_mm=round(requirement.net_requirement_mm, 2),
            duration_minutes=(
                round(duration.duration_minutes, 1) if duration is not None else None
            ),
            estimated_cost_mad=(
                round(cost.estimated_cost, 2) if cost is not None else None
            ),
            et0_mm_day=round(et0.et0_mm_day, 2),
            etc_mm_day=round(crop_water.etc_mm_day, 2),
            stress_level=balance.stress_level.value,
            recommendation=requirement.recommendation,
        )

    def _stage_for(
        self, field: Field, crop: CropParameters, weather: DailyWeather
    ) -> GrowthStageInfo:
        """Stade déclaré s'il existe, estimé sinon — et jamais confondus.

        Un stade déclaré par l'exploitant fait foi. Un stade estimé depuis la
        date de plantation est une hypothèse qui se trompe dès qu'une saison est
        atypique, et l'interface doit pouvoir le dire.
        """
        if field.declared_growth_stage is not None:
            return GrowthStageInfo(
                stage=GrowthStage(field.declared_growth_stage),
                source=StageSource.DECLARED,
                reference_date=weather.day,
                note_fr="Stade déclaré par l'exploitant.",
            )
        return estimate_growth_stage(
            crop, on_date=weather.day, planting_date=field.planting_date
        )

    def _to_decision(
        self,
        *,
        field: Field,
        crop: Crop,
        soil: SoilProfile,
        system: IrrigationSystem,
        moisture: SoilMoistureReading,
        weather: DailyWeather,
        et0: ET0Result,
        crop_water: CropWaterRequirement,
        balance: WaterBalanceResult,
        requirement: IrrigationRequirement,
        duration: IrrigationDuration | None,
        cost: WaterCost | None,
    ) -> Decision:
        """Met en forme le contrat commun. Ne calcule rien.

        Chaque pièce est nommée et typée. Un sac `**parts: object` conviendrait
        au vérificateur — il ne dirait rien — et c'est précisément le problème :
        c'est ici qu'une valeur météo pourrait se retrouver présentée comme une
        mesure de sonde, et le typage est ce qui rend l'erreur visible avant
        l'exécution.
        """
        quality = DataQuality()
        quality.add("weather", "Météo", True, source=weather.origin)
        quality.add("crop", "Culture", True, source=DataOrigin.REFERENCE_TABLE)
        quality.add("soil", "Sol", True, source=DataOrigin.REFERENCE_TABLE)
        quality.add("system", "Système d'irrigation", True, source=DataOrigin.REFERENCE_TABLE)
        quality.add("moisture", "Humidité du sol", True, source=moisture.data_origin)

        inputs = (
            DecisionInput(
                key="soil_moisture",
                label_fr="Humidité du sol",
                value=moisture.value_pct,
                unit="%",
                state=moisture.data_state,
                origin=moisture.data_origin,
            ),
            DecisionInput(
                key="temp_max",
                label_fr="Température maximale",
                value=weather.temp_max_c,
                unit="°C",
                state=weather.state,
                origin=weather.origin,
                source=weather.source,
            ),
            DecisionInput(
                key="et0",
                label_fr="ET0 de référence",
                value=round(et0.et0_mm_day, 2),
                unit="mm/j",
                state=DataState.DERIVED,
                origin=DataOrigin.MODEL,
                source=SOURCE_ENGINE,
            ),
            DecisionInput(
                key="kc",
                label_fr="Coefficient cultural Kc",
                value=round(crop_water.kc, 3),
                unit=None,
                # Un Kc lu dans la table est DERIVED ; un Kc interpolé sur un
                # stade estimé reste une inférence, et la puce doit le dire.
                state=(
                    DataState.INFERRED
                    if crop_water.stage_source is StageSource.ESTIMATED
                    else DataState.DERIVED
                ),
                origin=DataOrigin.REFERENCE_TABLE,
                source=SOURCE_FAO,
            ),
            DecisionInput(
                key="depletion",
                label_fr="Déficit racinaire",
                value=round(balance.depletion_mm, 1),
                unit="mm",
                state=DataState.DERIVED,
                origin=DataOrigin.MODEL,
                source=SOURCE_ENGINE,
            ),
        )

        unavailable: dict[str, str] = {}
        # Une sortie absente doit dire **pourquoi** elle l'est, et la raison doit
        # être vraie. Sans irrigation il n'y a pas de durée à calculer, ce qui
        # n'a rien à voir avec un débit manquant : annoncer « débit non
        # renseigné » sur une parcelle qui en a un serait un message faux, et un
        # message faux sur une absence est pire qu'un silence.
        is_applying = requirement.recommendation is Recommendation.IRRIGATE
        if is_applying:
            if duration is None:
                unavailable["duree"] = (
                    "Durée non calculable : le débit du système n'est pas renseigné "
                    "pour cette parcelle."
                )
            if cost is None:
                unavailable["cout"] = (
                    "Coût non calculable : aucun tarif de l'eau n'est renseigné pour "
                    "cette parcelle."
                )
        if crop.yield_response_factor_ky is None:
            unavailable["rendement"] = (
                crop.ky_absent_reason_fr
                or "Aucun coefficient Ky documenté : impact sur le rendement non estimé."
            )

        headline = self._headline(requirement, duration)
        level = reliability_level(quality.score)

        return Decision(
            domain=DecisionDomain.IRRIGATION,
            subject_id=field.code,
            subject_label_fr=f"Parcelle {field.code} — {field.name_fr}",
            headline_fr=headline,
            outcome_code=requirement.recommendation.value,
            inputs=inputs,
            calculation_steps_fr=self._calculation_steps(et0, crop_water, balance, requirement),
            assumptions_fr=(
                f"Culture : {crop.name_fr}. Sol : {soil.name_fr}. "
                f"Système : {system.name_fr} (efficience {fr_pct(system.efficiency, 0)}).",
                *crop_water.caveats_fr,
            ),
            evidence=(
                EvidenceItem(
                    label_fr="Météo du jour",
                    detail_fr=(
                        f"{fr(weather.temp_max_c, 1)} / {fr(weather.temp_min_c, 1)} °C, "
                        f"source {weather.source.label_fr}."
                    ),
                    state=weather.state,
                    source=weather.source,
                ),
                EvidenceItem(
                    label_fr="Humidité du sol",
                    detail_fr=(
                        f"{fr(moisture.value_pct, 1)} % relevé le "
                        f"{moisture.recorded_at:%d/%m à %H:%M}."
                    ),
                    state=moisture.data_state,
                    observed_at=moisture.recorded_at,
                ),
                EvidenceItem(
                    label_fr="Méthode d'ET0",
                    detail_fr=et0.method_label_fr,
                    state=DataState.DERIVED,
                    source=SOURCE_ENGINE,
                ),
            ),
            warnings_fr=tuple(requirement.warnings) + tuple(et0.warnings),
            reliability=level,
            unavailable_fr=unavailable,
        )

    @staticmethod
    def _calculation_steps(
        et0: ET0Result,
        crop_water: CropWaterRequirement,
        balance: WaterBalanceResult,
        requirement: IrrigationRequirement,
    ) -> tuple[str, ...]:
        """La chaîne entière, dans l'ordre où elle a été calculée.

        Le panneau « Pourquoi cette décision ? » montrait auparavant la seule
        dernière étape — la dose — ce qui laissait l'ET0, l'ETc et le bilan hors
        de portée du lecteur. Un exploitant qui conteste un chiffre conteste
        presque toujours une étape intermédiaire ; l'y renvoyer est tout l'objet
        du panneau.

        Rien n'est calculé ici : chaque nombre est **relu** depuis ce que les
        moteurs ont produit. Recalculer, fût-ce une soustraction pour la mise en
        forme, ferait exister deux versions du même chiffre.
        """
        steps: list[str] = []

        radiation = et0.intermediates.get("net_radiation_rn")
        deficit = et0.intermediates.get("vapour_pressure_deficit")
        et0_step = (
            f"ET0 = {fr(et0.et0_mm_day, 2)} mm/j par {et0.method_label_fr}"
        )
        if radiation is not None and deficit is not None:
            et0_step += (
                f" (FAO-56 éq. 6) : rayonnement net {fr(radiation, 1)} MJ/m²/j, "
                f"déficit de pression de vapeur {fr(deficit, 2)} kPa."
            )
        else:
            # Repli Hargreaves-Samani : l'équation citée doit être celle qui a
            # réellement tourné, sinon la trace décrit un calcul qui n'a pas eu
            # lieu.
            et0_step += " (FAO-56 éq. 52)."
        steps.append(et0_step)

        steps.append(
            f"ETc = ET0 × Kc = {fr(crop_water.et0_mm_day, 2)} × {fr(crop_water.kc, 2)} = "
            f"{fr(crop_water.etc_mm_day, 2)} mm/j (FAO-56 éq. 31). "
            f"{crop_water.kc_explanation_fr}"
        )
        steps.append(crop_water.root_depth_explanation_fr)
        steps.extend(balance.explanations)
        steps.extend(requirement.steps)

        # Numérotation à l'affichage : le panneau montre « 1. », « 2. »… et
        # l'utilisateur peut citer un numéro d'étape en appelant le support.
        return tuple(f"{index}. {step}" for index, step in enumerate(steps, start=1))

    def _headline(
        self, requirement: IrrigationRequirement, duration: IrrigationDuration | None
    ) -> str:
        label: str = requirement.recommendation_label_fr
        if requirement.recommendation is not Recommendation.IRRIGATE:
            return label
        volume = f"{fr(requirement.volume_m3, 1)} m³"
        if duration is not None:
            return (
                f"{label} : {volume} sur "
                f"{format_duration_fr(duration.duration_minutes)}."
            )
        return f"{label} : {volume} (durée non calculable, débit non renseigné)."
