"""Parcelles, irrigation, expéditions, vue générale.

La surface que l'interface consomme. Aucune requête n'est construite ici : la
couche API met en forme ce que les services rendent, et c'est ce qui garantit
qu'un même chiffre ne peut pas différer entre l'écran, l'outil du copilote et le
serveur MCP — les trois passent par le même service.

Deux choix qui se voient dans les réponses :

* une parcelle dont la recommandation est impossible sort **quand même**, avec
  `blocked_reason_fr`. Une parcelle absente d'une liste est indiscernable d'une
  parcelle qui va bien ;
* une capacité non livrée est annoncée **par le serveur** (`risk_analysis_fr`),
  pas écrite en dur dans l'interface. Le jour où le moteur arrive, l'écran cesse
  de l'annoncer sans qu'on le modifie.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.adapters.weather import build_weather_provider
from app.api.deps import (
    CurrentAudit,
    CurrentContext,
    CurrentRepository,
    CurrentSession,
)
from app.api.schemas import (
    AlternativeSchema,
    ExposedSegmentSchema,
    FieldCreate,
    FieldOut,
    IrrigationOut,
    MoistureCreate,
    MoistureOut,
    MoistureReadingOut,
    OverviewOut,
    ShipmentOut,
    ShipmentRiskSchema,
)
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.domain.irrigation.constants import RECOMMENDATION_FR, STRESS_LEVEL_FR, StressLevel
from app.domain.irrigation.irrigation import format_duration_fr
from app.services.farm_service import FarmService, FieldSummary, ShipmentSummary
from app.services.irrigation_service import IrrigationService
from app.services.logistics_service import LogisticsService
from app.services.onboarding_service import OnboardingService
from app.services.quota_service import QuotaService
from app.tools.business_tools import DISRUPTION_CAVEAT_FR

router = APIRouter(tags=["Exploitation"])

#: Ce que la plateforme ne sait pas encore faire, dit une fois, ici.
#: L'interface l'affiche ; elle ne l'invente pas.
_NOT_DELIVERED_FR = [
    "Stocks, fournisseurs et couverture (non livrés)",
]


def _field_out(summary: FieldSummary) -> FieldOut:
    moisture = None
    if (
        summary.moisture_pct is not None
        and summary.moisture_recorded_at is not None
        and summary.moisture_state is not None
        and summary.moisture_origin is not None
    ):
        moisture = MoistureOut(
            value_pct=summary.moisture_pct,
            recorded_at=summary.moisture_recorded_at,
            state=summary.moisture_state,
            state_label_fr=summary.moisture_state.label_fr,
            origin=summary.moisture_origin,
            origin_label_fr=summary.moisture_origin.label_fr,
        )
    return FieldOut(
        code=summary.code,
        name_fr=summary.name_fr,
        site_name_fr=summary.site_name_fr,
        area_ha=summary.area_ha,
        latitude=summary.latitude,
        longitude=summary.longitude,
        boundary_geojson=summary.boundary_geojson,
        crop_code=summary.crop_code,
        crop_name_fr=summary.crop_name_fr,
        soil_name_fr=summary.soil_name_fr,
        system_name_fr=summary.system_name_fr,
        has_flow_rate=summary.has_flow_rate,
        has_water_tariff=summary.has_water_tariff,
        moisture=moisture,
        blocked_reason_fr=summary.blocked_reason_fr,
    )


def _shipment_out(summary: ShipmentSummary) -> ShipmentOut:
    return ShipmentOut(
        reference=summary.reference,
        product_name_fr=summary.product_name_fr,
        volume_tonnes=summary.volume_tonnes,
        transport_mode=summary.transport_mode,
        transport_mode_label_fr=summary.transport_mode_label_fr,
        status=summary.status,
        status_label_fr=summary.status_label_fr,
        origin_site_fr=summary.origin_site_fr,
        destination_site_fr=summary.destination_site_fr,
        origin_latitude=summary.origin_latitude,
        origin_longitude=summary.origin_longitude,
        destination_latitude=summary.destination_latitude,
        destination_longitude=summary.destination_longitude,
        departure_at=summary.departure_at,
        sla_deadline_at=summary.sla_deadline_at,
        hours_to_deadline=round(FarmService.hours_until(summary.sla_deadline_at), 1),
        requires_cold_chain=summary.requires_cold_chain,
        source_field_code=summary.source_field_code,
        # Plus rien à annoncer comme absent sur une expédition : l'analyse est
        # livrée et vit sur sa propre route. Le champ reste, parce que la
        # prochaine capacité manquante s'y logera.
        risk_analysis_fr=None,
    )


@router.get("/parcelles", response_model=list[FieldOut], summary="Lister les parcelles")
async def list_fields(session: CurrentSession, _: CurrentContext) -> list[FieldOut]:
    return [_field_out(s) for s in await FarmService(session).list_fields()]


@router.post(
    "/parcelles",
    response_model=FieldOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une parcelle",
)
async def create_field(
    payload: FieldCreate,
    session: CurrentSession,
    context: CurrentContext,
    audit: CurrentAudit,
) -> FieldOut:
    """Crée une parcelle, après vérification du plafond du plan.

    Le plafond est vérifié **avant** l'écriture : refuser après aurait laissé
    une parcelle qu'il aurait fallu supprimer, c'est-à-dire décider à la place
    de l'exploitant.
    """
    await QuotaService(session, context).check_stock("field")
    service = OnboardingService(session, context)
    field = await service.create_field(
        code=payload.code,
        name_fr=payload.name_fr,
        site_code=payload.site_code,
        area_ha=payload.area_ha,
        latitude=payload.latitude,
        longitude=payload.longitude,
        crop_code=payload.crop_code,
        soil_code=payload.soil_code,
        irrigation_system_code=payload.irrigation_system_code,
        planting_date=payload.planting_date,
        flow_rate_m3_per_hour=payload.flow_rate_m3_per_hour,
        water_cost_per_m3=payload.water_cost_per_m3,
        boundary_geojson=payload.boundary_geojson,
    )
    await audit.record(
        context,
        action="field:create",
        resource_type="FIELD",
        resource_id=str(field.id),
        outcome="SUCCESS",
        detail={"code": field.code},
    )
    # Relu par le même chemin que la liste : une fiche construite ici
    # divergerait de celle de l'écran dès la première colonne ajoutée.
    summaries = await FarmService(session).list_fields()
    created = next(s for s in summaries if s.code == field.code)
    return _field_out(created)


@router.post(
    "/parcelles/{code}/humidite",
    response_model=MoistureReadingOut,
    status_code=status.HTTP_201_CREATED,
    summary="Saisir un relevé d'humidité",
)
async def create_moisture_reading(
    code: str,
    payload: MoistureCreate,
    session: CurrentSession,
    context: CurrentContext,
    audit: CurrentAudit,
) -> MoistureReadingOut:
    """Enregistre une mesure saisie à la main.

    Le couple provenance est écrit par le serveur — `OBSERVED` / saisie
    manuelle. `MoistureCreate` refuse les champs inconnus, donc un client qui
    tenterait de déclarer « sonde » reçoit une 422 plutôt qu'un silence.
    """
    reading = await OnboardingService(session, context).record_moisture(
        field_code=code,
        value_pct=payload.value_pct,
        depth_cm=payload.depth_cm,
        recorded_at=payload.recorded_at,
    )
    await audit.record(
        context,
        action="moisture:create",
        resource_type="SOIL_MOISTURE_READING",
        resource_id=str(reading.id),
        outcome="SUCCESS",
        detail={"field_code": code.upper()},
    )
    return MoistureReadingOut(
        field_code=code.strip().upper(),
        value_pct=reading.value_pct,
        depth_cm=reading.depth_cm,
        recorded_at=reading.recorded_at,
        state=reading.data_state,
        state_label_fr=reading.data_state.label_fr,
        origin=reading.data_origin,
        origin_label_fr=reading.data_origin.label_fr,
    )


@router.get(
    "/parcelles/{code}/irrigation",
    response_model=IrrigationOut,
    summary="Recommandation d'irrigation d'une parcelle",
)
async def irrigation_for_field(
    code: str, session: CurrentSession, repository: CurrentRepository
) -> IrrigationOut:
    """Lance le moteur FAO-56 pour une parcelle.

    Le fournisseur météo suit l'organisation, jamais un réglage global : une
    organisation de démonstration ne reçoit pas de prévision réelle
    (décision 0011).
    """
    tenant = await repository.current_tenant()
    provider = build_weather_provider(
        "offline" if tenant.is_demo or get_settings().demo_mode else "open-meteo"
    )
    outcome = await IrrigationService(session, provider).decide(code)
    decision = outcome.decision
    stress = StressLevel(outcome.stress_level)
    return IrrigationOut(
        field_code=decision.subject_id,
        field_name_fr=decision.subject_label_fr,
        recommendation=outcome.recommendation.value,
        recommendation_label_fr=RECOMMENDATION_FR[outcome.recommendation],
        headline_fr=decision.headline_fr,
        water_volume_m3=outcome.volume_m3,
        net_requirement_mm=outcome.net_requirement_mm,
        duration_minutes=outcome.duration_minutes,
        duration_label_fr=(
            format_duration_fr(outcome.duration_minutes)
            if outcome.duration_minutes is not None
            else None
        ),
        estimated_cost_mad=outcome.estimated_cost_mad,
        et0_mm_day=outcome.et0_mm_day,
        etc_mm_day=outcome.etc_mm_day,
        stress_level=stress.value,
        stress_label_fr=STRESS_LEVEL_FR[stress],
        reliability=decision.reliability,
        reliability_label_fr=(
            decision.reliability.label_fr if decision.reliability else None
        ),
        decision=decision.to_dict(),
    )


@router.get(
    "/expeditions", response_model=list[ShipmentOut], summary="Lister les expéditions"
)
async def list_shipments(session: CurrentSession, _: CurrentContext) -> list[ShipmentOut]:
    return [_shipment_out(s) for s in await FarmService(session).list_shipments()]


@router.get(
    "/expeditions/{reference}",
    response_model=ShipmentOut,
    summary="Fiche d'une expédition",
)
async def get_shipment(
    reference: str, session: CurrentSession, _: CurrentContext
) -> ShipmentOut:
    return _shipment_out(await FarmService(session).get_shipment(reference))


@router.get(
    "/expeditions/{reference}/risque",
    response_model=ShipmentRiskSchema,
    summary="Analyse de risque d'une expédition",
)
async def shipment_risk(
    reference: str, session: CurrentSession, repository: CurrentRepository
) -> ShipmentRiskSchema:
    """Exposition tronçon par tronçon, alternatives classées, options écartées.

    Le fournisseur météo suit l'organisation, comme pour l'irrigation : une
    organisation de démonstration ne reçoit pas de prévision réelle
    (décision 0011).
    """
    tenant = await repository.current_tenant()
    provider = build_weather_provider(
        "offline" if tenant.is_demo or get_settings().demo_mode else "open-meteo"
    )
    outcome = await LogisticsService(session, provider).analyse(reference)
    decision = outcome.decision
    current = outcome.current_plan
    assessment = current.assessment
    if assessment is None or assessment.sla_deadline_at is None:
        # Une expédition porte toujours une échéance en base ; si elle manque
        # ici, l'analyse est incomplète et on le dit plutôt que de substituer
        # une date de repli — une fausse échéance est pire qu'aucune.
        raise NotFoundError(
            f"Le plan actuel de « {reference} » n'a pas pu être évalué.",
            remedy_fr="Aucune analyse n'est produite.",
        )

    by_id = {option.id: option for option in outcome.alternatives}
    baseline_cost = current.total_cost_mad
    baseline_duration = current.total_duration_hours
    baseline_risk = assessment.risk_score

    alternatives: list[AlternativeSchema] = []
    for considered in decision.alternatives:
        option = by_id.get(considered.id)
        option_assessment = option.assessment if option else None
        usable = considered.is_feasible and option is not None
        cost = option.total_cost_mad if usable and option else None
        duration = option.total_duration_hours if usable and option else None
        alternatives.append(
            AlternativeSchema(
                id=considered.id,
                label_fr=considered.label_fr,
                description_fr=considered.description_fr,
                kind=option.kind if option else "UNKNOWN",
                is_current_plan=bool(option and option.is_current_plan),
                is_feasible=considered.is_feasible,
                rank=considered.rank,
                is_recommended=considered.is_recommended,
                cost_mad=cost,
                cost_delta_mad=(
                    round(cost - baseline_cost, 0)
                    if cost is not None and baseline_cost is not None
                    else None
                ),
                duration_hours=duration,
                duration_delta_hours=(
                    round(duration - baseline_duration, 2)
                    if duration is not None and baseline_duration is not None
                    else None
                ),
                departure_at=option_assessment.departure_at if option_assessment else None,
                arrival_at=option.arrival_at if usable and option else None,
                sla_margin_hours=option.sla_margin_hours if usable and option else None,
                risk_level=(
                    option_assessment.risk_level.value if usable and option_assessment else None
                ),
                risk_level_label_fr=(
                    option_assessment.risk_level.label_fr
                    if usable and option_assessment
                    else None
                ),
                risk_delta=(
                    round(option_assessment.risk_score - baseline_risk, 3)
                    if usable and option_assessment
                    else None
                ),
                exposure_fraction=(
                    option_assessment.exposure_fraction
                    if usable and option_assessment
                    else None
                ),
                rejection_reasons_fr=[r.message_fr for r in considered.rejection_reasons],
                path_lonlat=(
                    [[lon, lat] for lon, lat in option_assessment.route.path_lonlat]
                    if usable and option_assessment
                    else []
                ),
            )
        )

    return ShipmentRiskSchema(
        reference=decision.subject_id,
        headline_fr=decision.headline_fr,
        outcome_code=decision.outcome_code,
        profile_fr=outcome.ranking.profile.label_fr,
        profile_rationale_fr=outcome.ranking.profile.rationale_fr,
        risk_level=assessment.risk_level.value,
        risk_level_label_fr=assessment.risk_level.label_fr,
        exposure_fraction=assessment.exposure_fraction,
        exposed_distance_km=assessment.exposed_distance_km,
        disruption_indicator=assessment.disruption_indicator,
        disruption_caveat_fr=DISRUPTION_CAVEAT_FR,
        departure_at=assessment.departure_at,
        sla_deadline_at=assessment.sla_deadline_at,
        sla_compliant=assessment.sla_compliant,
        exposed_segments=[
            ExposedSegmentSchema(
                from_name_fr=segment.from_name_fr,
                to_name_fr=segment.to_name_fr,
                road_ref=segment.road_ref,
                distance_km=segment.distance_km,
                hours_from_departure=segment.hours_from_departure,
                entry_at=segment.entry_at,
                exit_at=segment.exit_at,
                risk_level=segment.level.value,
                risk_level_label_fr=segment.level.label_fr,
                reasons_fr=list(segment.reasons_fr),
                latitudes=[
                    assessment.route.segments[segment.index].from_latitude,
                    assessment.route.segments[segment.index].to_latitude,
                ],
                longitudes=[
                    assessment.route.segments[segment.index].from_longitude,
                    assessment.route.segments[segment.index].to_longitude,
                ],
            )
            for segment in assessment.segments
            if segment.is_exposed
        ],
        alternatives=alternatives,
        reliability=decision.reliability,
        reliability_label_fr=(
            decision.reliability.label_fr if decision.reliability else None
        ),
        decision=decision.to_dict(),
    )


@router.get("/vue-generale", response_model=OverviewOut, summary="Vue générale")
async def overview(session: CurrentSession, repository: CurrentRepository) -> OverviewOut:
    """Des décomptes, jamais un indice composite.

    Chaque nombre se retrouve à la main depuis un autre écran. Un « indice de
    santé » agrégé serait un chiffre que personne ne peut contester, donc un
    chiffre que personne ne devrait croire.

    « Parcelles à irriguer » exécute le moteur pour chaque parcelle éligible.
    C'est plus coûteux qu'un seuil sur la dernière humidité, et c'est le point :
    le décompte de l'accueil et la fiche de la parcelle sortent du même calcul,
    donc ils ne peuvent pas se contredire.
    """
    tenant = await repository.current_tenant()
    farm = FarmService(session)
    fields = await farm.list_fields()
    provider = build_weather_provider(
        "offline" if tenant.is_demo or get_settings().demo_mode else "open-meteo"
    )
    irrigation = IrrigationService(session, provider)

    to_irrigate = 0
    for field in fields:
        if field.blocked_reason_fr is not None:
            continue
        outcome = await irrigation.decide(field.code)
        if outcome.recommendation.value == "IRRIGATE":
            to_irrigate += 1

    shipments = await farm.list_shipments()
    in_transit = sum(1 for s in shipments if s.status == "IN_TRANSIT")
    due_soon = sum(
        1 for s in shipments if 0 <= FarmService.hours_until(s.sla_deadline_at) <= 24
    )

    return OverviewOut(
        tenant_name_fr=tenant.name,
        is_demo=tenant.is_demo,
        fields_total=len(fields),
        fields_to_irrigate=to_irrigate,
        fields_blocked=sum(1 for f in fields if f.blocked_reason_fr is not None),
        shipments_in_transit=in_transit,
        shipments_due_within_24h=due_soon,
        not_delivered_fr=list(_NOT_DELIVERED_FR),
    )
