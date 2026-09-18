"""Les capacités métier, définies une fois.

Les outils sont **taillés pour la décision**, pas pour le calcul. Un seul
`calculate_irrigation_requirement` résout la culture, le sol, le système, le
stade, l'humidité et la météo, puis enchaîne ET0 → ETc → bilan → besoin → volume
→ durée → coût, et rend une recommandation structurée.

C'est délibéré et cela doit le rester : exposer les étapes intermédiaires comme
autant d'outils inviterait le modèle à les assembler lui-même, c'est-à-dire à
faire exactement l'arithmétique qui lui est interdite. Des outils gros et
décisionnels ; pas une API de calculatrice.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.adapters.weather import build_weather_provider
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.db.base import Crop, Shipment, Site, Tenant
from app.db.base import Field as FieldModel
from app.domain.decision import Decision, HumanVerdict
from app.domain.enums import ORIGIN_BY_RELIABILITY, DataOrigin, DataState, UserRole
from app.services.irrigation_service import IrrigationService
from app.services.logistics_service import LogisticsService
from app.tools.registry import ToolContext, tool

__all__ = ["ALL_TOOL_NAMES"]


class _In(BaseModel):
    """Base des entrées d'outil.

    `extra="forbid"` est ce qui rend structurelle la règle « le tenant n'est
    jamais un paramètre » : un modèle qui tenterait `{"tenant_id": "..."}`
    reçoit une erreur de validation, pas un champ ignoré en silence.
    """

    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Parcelles
# ---------------------------------------------------------------------------
class ListFieldsInput(_In):
    pass


class FieldSummary(_Out):
    code: str
    name_fr: str
    area_ha: float
    crop_code: str | None
    crop_name_fr: str | None
    has_flow_rate: bool


class ListFieldsOutput(_Out):
    fields: list[FieldSummary]
    count: int


@tool(
    name="list_fields",
    description_fr=(
        "Liste les parcelles de l'organisation avec leur culture et leur surface. "
        "À utiliser quand l'utilisateur désigne une parcelle de façon ambiguë, ou "
        "demande une vue d'ensemble avant de choisir."
    ),
    input_model=ListFieldsInput,
    output_model=ListFieldsOutput,
    decision_support_fr="Identifier la parcelle dont il est question.",
)
async def _list_fields(_: ListFieldsInput, ctx: ToolContext) -> ListFieldsOutput:
    rows = list((await ctx.session.execute(select(FieldModel))).scalars())
    crops = {
        c.id: c
        for c in (await ctx.session.execute(select(Crop))).scalars()
    }
    summaries = [
        FieldSummary(
            code=f.code,
            name_fr=f.name_fr,
            area_ha=f.area_ha,
            crop_code=crops[f.crop_id].code if f.crop_id in crops else None,
            crop_name_fr=crops[f.crop_id].name_fr if f.crop_id in crops else None,
            has_flow_rate=f.flow_rate_m3_per_hour is not None,
        )
        for f in sorted(rows, key=lambda f: f.code)
    ]
    return ListFieldsOutput(fields=summaries, count=len(summaries))


# ---------------------------------------------------------------------------
# Irrigation — un seul outil, toute la chaîne
# ---------------------------------------------------------------------------
class IrrigationInput(_In):
    field_code: str = Field(
        min_length=1,
        max_length=40,
        description="Code de la parcelle, par exemple « P03 ».",
    )


class ProvenanceOut(_Out):
    key: str
    label_fr: str
    value: float | str | None
    unit: str | None
    state: str
    state_label_fr: str
    origin: str
    origin_label_fr: str


class IrrigationOutput(_Out):
    """Recommandation d'irrigation, structurée.

    Aucun champ n'est une phrase toute faite portant un chiffre : le modèle
    relaie ces valeurs, il ne les retape pas. `duration_minutes` et
    `estimated_cost_mad` valent `null` quand l'entrée manque, et
    `unavailable_fr` dit pourquoi.
    """

    field_code: str
    recommendation: str
    recommendation_label_fr: str
    headline_fr: str
    water_volume_m3: float
    net_requirement_mm: float
    duration_minutes: float | None
    estimated_cost_mad: float | None
    et0_mm_day: float
    etc_mm_day: float
    stress_level: str
    reliability_label_fr: str | None
    inputs: list[ProvenanceOut]
    calculation_steps_fr: list[str]
    assumptions_fr: list[str]
    warnings_fr: list[str]
    unavailable_fr: dict[str, str]


async def _weather_provider(ctx: ToolContext) -> Any:
    """Le fournisseur de météo suit **l'organisation**, pas un drapeau global.

    Une organisation de démonstration ne reçoit jamais de prévision réelle.
    Ce n'est pas une commodité : un jeu fabriqué dans lequel on injecterait une
    observation authentique deviendrait partiellement vrai, et plus personne ne
    saurait quelle valeur relève de laquelle. Le fournisseur hors ligne étiquette
    tout en « Simulé » jusqu'à l'écran — ce n'est pas un repli discret.

    La lecture de `is_demo` passe sous politique : une organisation ne peut lire
    que sa propre ligne, donc ce choix ne peut pas être détourné par un appelant.

    `demo_mode` reste une bascule d'installation, lue dans la configuration du
    processus : elle force le mode hors ligne pour toutes les organisations d'un
    poste de développement, et `_production_guards` la refuse en production.
    """
    is_demo = (
        await ctx.session.execute(
            select(Tenant.is_demo).where(Tenant.id == ctx.request.tenant_id)
        )
    ).scalar_one()
    if is_demo or get_settings().demo_mode:
        return build_weather_provider("offline")
    return build_weather_provider("open-meteo")


@tool(
    name="calculate_irrigation_requirement",
    description_fr=(
        "Calcule la recommandation d'irrigation complète d'une parcelle : ET0 "
        "(FAO-56), ETc, bilan hydrique racinaire, dose nette et brute, volume, "
        "durée et coût. C'est l'outil à appeler pour « dois-je irriguer ? », "
        "« quelle quantité ? » ou « pourquoi cette parcelle a-t-elle besoin de "
        "plus d'eau ? ». Il fait toute la chaîne de calcul : ne tente jamais de "
        "recomposer ces nombres toi-même."
    ),
    input_model=IrrigationInput,
    output_model=IrrigationOutput,
    decision_support_fr="Irriguer aujourd'hui, reporter, ou surveiller.",
)
async def _calculate_irrigation_requirement(
    payload: IrrigationInput, ctx: ToolContext
) -> IrrigationOutput:
    service = IrrigationService(ctx.session, await _weather_provider(ctx))
    outcome = await service.decide(payload.field_code)
    decision = outcome.decision
    return IrrigationOutput(
        field_code=decision.subject_id,
        recommendation=outcome.recommendation.value,
        recommendation_label_fr=outcome.recommendation.name,
        headline_fr=decision.headline_fr,
        water_volume_m3=outcome.volume_m3,
        net_requirement_mm=outcome.net_requirement_mm,
        duration_minutes=outcome.duration_minutes,
        estimated_cost_mad=outcome.estimated_cost_mad,
        et0_mm_day=outcome.et0_mm_day,
        etc_mm_day=outcome.etc_mm_day,
        stress_level=outcome.stress_level,
        reliability_label_fr=(
            decision.reliability.label_fr if decision.reliability else None
        ),
        inputs=[
            ProvenanceOut(
                key=i.key,
                label_fr=i.label_fr,
                value=i.value,
                unit=i.unit,
                state=i.state.value,
                state_label_fr=i.state.label_fr,
                origin=i.origin.value,
                origin_label_fr=i.origin.label_fr,
            )
            for i in decision.inputs
        ],
        calculation_steps_fr=list(decision.calculation_steps_fr),
        assumptions_fr=list(decision.assumptions_fr),
        warnings_fr=list(decision.warnings_fr),
        unavailable_fr=dict(decision.unavailable_fr),
    )


# ---------------------------------------------------------------------------
# Expéditions
# ---------------------------------------------------------------------------
class ShipmentInput(_In):
    reference: str = Field(min_length=1, max_length=40)


class ShipmentOutput(_Out):
    reference: str
    product_name_fr: str | None
    volume_tonnes: float
    transport_mode: str
    status: str
    status_label_fr: str
    origin_site_fr: str
    destination_site_fr: str
    departure_at: str
    sla_deadline_at: str
    requires_cold_chain: bool | None
    source_field_code: str | None


@tool(
    name="get_shipment",
    description_fr=(
        "Renvoie la fiche d'une expédition : produit, volume, mode de transport, "
        "origine, destination, départ et échéance de service. Premier appel pour "
        "toute question portant sur un transport."
    ),
    input_model=ShipmentInput,
    output_model=ShipmentOutput,
    decision_support_fr="Identifier l'expédition et ses contraintes.",
)
async def _get_shipment(payload: ShipmentInput, ctx: ToolContext) -> ShipmentOutput:
    shipment = (
        await ctx.session.execute(
            select(Shipment).where(Shipment.reference == payload.reference.upper())
        )
    ).scalar_one_or_none()
    if shipment is None:
        raise NotFoundError(
            f"Expédition « {payload.reference} » introuvable.",
            remedy_fr="Vérifiez la référence dans la page Expéditions.",
        )

    sites = {
        s.id: s
        for s in (await ctx.session.execute(select(Site))).scalars()
    }
    product_name = None
    cold_chain: bool | None = None
    if shipment.product_id is not None:
        from app.db.base import Product

        product = (
            await ctx.session.execute(
                select(Product).where(Product.id == shipment.product_id)
            )
        ).scalar_one_or_none()
        if product is not None:
            product_name = product.name_fr
            if product.crop_id is not None:
                crop = (
                    await ctx.session.execute(
                        select(Crop).where(Crop.id == product.crop_id)
                    )
                ).scalar_one_or_none()
                cold_chain = crop.requires_cold_chain if crop else None

    source_field_code = None
    if shipment.source_field_id is not None:
        field = (
            await ctx.session.execute(
                select(FieldModel).where(FieldModel.id == shipment.source_field_id)
            )
        ).scalar_one_or_none()
        source_field_code = field.code if field else None

    return ShipmentOutput(
        reference=shipment.reference,
        product_name_fr=product_name,
        volume_tonnes=shipment.volume_tonnes,
        transport_mode=shipment.transport_mode.value,
        status=shipment.status.value,
        status_label_fr=shipment.status.label_fr,
        origin_site_fr=sites[shipment.origin_site_id].name_fr,
        destination_site_fr=sites[shipment.destination_site_id].name_fr,
        departure_at=shipment.departure_at.isoformat(),
        sla_deadline_at=shipment.sla_deadline_at.isoformat(),
        requires_cold_chain=cold_chain,
        source_field_code=source_field_code,
    )


# ---------------------------------------------------------------------------
# Risque logistique — un seul outil, toute la chaîne
# ---------------------------------------------------------------------------
class ShipmentRiskInput(_In):
    reference: str = Field(
        min_length=1, max_length=40,
        description="Référence de l'expédition, par exemple « EXP-1842 ».",
    )


class ExposedSegmentOut(_Out):
    from_name_fr: str
    to_name_fr: str
    road_ref: str
    distance_km: float
    hours_from_departure: float
    entry_at: str
    risk_level: str
    risk_level_label_fr: str
    reasons_fr: list[str]


class AlternativeOut(_Out):
    """Une option, retenue ou écartée.

    Une option écartée n'a **aucun chiffre** : coût, durée et marge valent
    `null`. Les remplir la ferait apparaître dans un tableau comparatif comme un
    choix possible, ce qu'elle n'est pas.
    """

    id: str
    label_fr: str
    description_fr: str
    kind: str
    is_current_plan: bool
    is_feasible: bool
    rank: int | None
    is_recommended: bool
    cost_mad: float | None
    duration_hours: float | None
    sla_margin_hours: float | None
    risk_level: str | None
    risk_level_label_fr: str | None
    exposure_fraction: float | None
    rejection_reasons_fr: list[str]


class ShipmentRiskOutput(_Out):
    reference: str
    headline_fr: str
    outcome_code: str
    profile_fr: str
    risk_level: str
    risk_level_label_fr: str
    exposure_fraction: float
    exposed_distance_km: float
    disruption_indicator: float
    #: Le libellé qui **doit** accompagner l'indicateur. Rendu par le serveur
    #: pour qu'aucun client ne puisse l'afficher en le présentant comme une
    #: probabilité.
    disruption_caveat_fr: str
    current_cost_mad: float
    current_duration_hours: float
    current_arrival_at: str
    sla_deadline_at: str
    sla_compliant: bool
    exposed_segments: list[ExposedSegmentOut]
    alternatives: list[AlternativeOut]
    inputs: list[ProvenanceOut]
    calculation_steps_fr: list[str]
    assumptions_fr: list[str]
    warnings_fr: list[str]
    tradeoffs_fr: list[str]
    reliability_label_fr: str | None


DISRUPTION_CAVEAT_FR = (
    "Indicateur comparatif dérivé de règles explicites, destiné à classer des "
    "itinéraires entre eux. Ce n'est pas une probabilité : aucun historique "
    "d'incidents marocains ne l'a calibré."
)


@tool(
    name="analyze_shipment_risk",
    description_fr=(
        "Analyse le risque d'une expédition et propose des alternatives : "
        "exposition de chaque tronçon **pendant** sa traversée, itinéraires de "
        "repli, décalages de départ, coût, durée et respect de l'échéance. "
        "C'est l'outil à appeler pour « mon transport est-il à risque ? », "
        "« que faire face à la perturbation ? » ou « faut-il partir plus tôt ? ». "
        "Il fait toute la chaîne : ne recompose jamais ces nombres toi-même."
    ),
    input_model=ShipmentRiskInput,
    output_model=ShipmentRiskOutput,
    decision_support_fr="Maintenir le plan, changer d'itinéraire, ou décaler le départ.",
)
async def _analyze_shipment_risk(
    payload: ShipmentRiskInput, ctx: ToolContext
) -> ShipmentRiskOutput:
    service = LogisticsService(ctx.session, await _weather_provider(ctx))
    outcome = await service.analyse(payload.reference)
    decision = outcome.decision
    current = outcome.current_plan
    assessment = current.assessment
    if assessment is None:
        raise NotFoundError(
            f"Le plan actuel de « {payload.reference} » n'a pas pu être évalué.",
            remedy_fr="Aucune analyse n'est produite.",
        )

    by_id = {option.id: option for option in outcome.alternatives}
    alternatives: list[AlternativeOut] = []
    for considered in decision.alternatives:
        option = by_id.get(considered.id)
        option_assessment = option.assessment if option else None
        alternatives.append(
            AlternativeOut(
                id=considered.id,
                label_fr=considered.label_fr,
                description_fr=considered.description_fr,
                kind=option.kind if option else "UNKNOWN",
                is_current_plan=bool(option and option.is_current_plan),
                is_feasible=considered.is_feasible,
                rank=considered.rank,
                is_recommended=considered.is_recommended,
                cost_mad=option.total_cost_mad if considered.is_feasible and option else None,
                duration_hours=(
                    option.total_duration_hours if considered.is_feasible and option else None
                ),
                sla_margin_hours=(
                    option.sla_margin_hours if considered.is_feasible and option else None
                ),
                risk_level=(
                    option_assessment.risk_level.value
                    if considered.is_feasible and option_assessment
                    else None
                ),
                risk_level_label_fr=(
                    option_assessment.risk_level.label_fr
                    if considered.is_feasible and option_assessment
                    else None
                ),
                exposure_fraction=(
                    option_assessment.exposure_fraction
                    if considered.is_feasible and option_assessment
                    else None
                ),
                rejection_reasons_fr=[r.message_fr for r in considered.rejection_reasons],
            )
        )

    return ShipmentRiskOutput(
        reference=decision.subject_id,
        headline_fr=decision.headline_fr,
        outcome_code=decision.outcome_code,
        profile_fr=outcome.ranking.profile.label_fr,
        risk_level=assessment.risk_level.value,
        risk_level_label_fr=assessment.risk_level.label_fr,
        exposure_fraction=assessment.exposure_fraction,
        exposed_distance_km=assessment.exposed_distance_km,
        disruption_indicator=assessment.disruption_indicator,
        disruption_caveat_fr=DISRUPTION_CAVEAT_FR,
        current_cost_mad=assessment.cost.total_mad,
        current_duration_hours=assessment.adjusted_duration_hours,
        current_arrival_at=assessment.estimated_arrival_at.isoformat(),
        sla_deadline_at=(
            assessment.sla_deadline_at.isoformat() if assessment.sla_deadline_at else ""
        ),
        sla_compliant=assessment.sla_compliant,
        exposed_segments=[
            ExposedSegmentOut(
                from_name_fr=segment.from_name_fr,
                to_name_fr=segment.to_name_fr,
                road_ref=segment.road_ref,
                distance_km=segment.distance_km,
                hours_from_departure=segment.hours_from_departure,
                entry_at=segment.entry_at.isoformat(),
                risk_level=segment.level.value,
                risk_level_label_fr=segment.level.label_fr,
                reasons_fr=list(segment.reasons_fr),
            )
            for segment in assessment.segments
            if segment.is_exposed
        ],
        alternatives=alternatives,
        inputs=[
            ProvenanceOut(
                key=i.key,
                label_fr=i.label_fr,
                value=i.value,
                unit=i.unit,
                state=i.state.value,
                state_label_fr=i.state.label_fr,
                origin=i.origin.value,
                origin_label_fr=i.origin.label_fr,
            )
            for i in decision.inputs
        ],
        calculation_steps_fr=list(decision.calculation_steps_fr),
        assumptions_fr=list(decision.assumptions_fr),
        warnings_fr=list(decision.warnings_fr),
        tradeoffs_fr=list(decision.tradeoffs_fr),
        reliability_label_fr=(
            decision.reliability.label_fr if decision.reliability else None
        ),
    )


# ---------------------------------------------------------------------------
# Proposition — le seul outil d'écriture, et il n'exécute rien
# ---------------------------------------------------------------------------
class RecommendationInput(_In):
    subject_id: str = Field(
        min_length=1, max_length=120,
        description="Code de la parcelle ou référence de l'expédition concernée.",
    )
    headline_fr: str = Field(min_length=1, max_length=500)
    rationale_fr: str = Field(min_length=1, max_length=2000)
    domain: Literal["IRRIGATION", "LOGISTICS"] = Field(
        description="Domaine de la décision proposée."
    )


class RecommendationOutput(_Out):
    recommendation_id: str
    recorded: bool
    verdict: str
    verdict_label_fr: str
    notice_fr: str


@tool(
    name="create_recommendation",
    description_fr=(
        "Enregistre une proposition de décision **en attente de validation "
        "humaine**. N'exécute aucune action : n'irrigue rien, ne déplace aucune "
        "expédition, n'engage aucune dépense. À n'utiliser que si l'utilisateur "
        "demande explicitement de formaliser la décision."
    ),
    input_model=RecommendationInput,
    output_model=RecommendationOutput,
    allowed_roles=(
        UserRole.ADMIN,
        UserRole.AGRONOME,
        UserRole.OPERATIONS_MANAGER,
        UserRole.SUPPLY_CHAIN_MANAGER,
    ),
    read_only=False,
    decision_support_fr="Formaliser une proposition, sans l'appliquer.",
)
async def _create_recommendation(
    payload: RecommendationInput, ctx: ToolContext
) -> RecommendationOutput:
    """Enregistre réellement.

    Cet outil rendait auparavant `recorded=true` sans rien écrire. Un outil qui
    se trompe sur son propre effet est pire qu'un outil absent : le modèle
    annonce à l'utilisateur que sa décision est consignée, l'utilisateur le
    croit, et rien n'existe.

    La proposition est **reconstruite depuis les moteurs**, jamais rédigée par le
    modèle : le `payload` figé porte la décision calculée, avec sa provenance et
    ses étapes. Sans cela, la table conserverait la prose du modèle sous le nom
    de décision — et la question « qu'avions-nous conseillé ? » recevrait une
    réponse inventée des mois plus tard.
    """
    from app.domain.decision import DecisionDomain
    from app.services.recommendation_service import RecommendationService

    domain = DecisionDomain(payload.domain)
    if domain is DecisionDomain.IRRIGATION:
        service = IrrigationService(ctx.session, await _weather_provider(ctx))
        decision = (await service.decide(payload.subject_id)).decision
    else:
        logistics = LogisticsService(ctx.session, await _weather_provider(ctx))
        decision = (await logistics.analyse(payload.subject_id)).decision

    # La provenance de la recommandation est celle de ses entrées : une
    # proposition tirée d'un jeu de démonstration ne doit pas se confondre avec
    # une proposition tirée de mesures.
    weakest = _weakest_origin(decision)
    recorded = await RecommendationService(ctx.session, ctx.request).record(
        decision,
        rationale_fr=payload.rationale_fr,
        data_state=DataState.DERIVED,
        data_origin=weakest,
    )

    return RecommendationOutput(
        recommendation_id=str(recorded.id),
        recorded=True,
        verdict=HumanVerdict.PENDING.value,
        verdict_label_fr=HumanVerdict.PENDING.label_fr,
        notice_fr=(
            "Proposition enregistrée en attente de validation. Aucune action n'a "
            "été déclenchée : l'irrigation, le départ et la dépense restent à la "
            "main d'un responsable."
        ),
    )


def _weakest_origin(decision: Decision) -> DataOrigin:
    """L'origine la moins fiable parmi les entrées de la décision.

    Une recommandation ne peut pas être plus fiable que sa plus faible entrée.
    Retenir la meilleure — ou une valeur par défaut — ferait qu'une proposition
    fondée sur une humidité de démonstration s'archiverait comme fondée sur une
    mesure.
    """
    origins = [i.origin for i in decision.inputs if i.value is not None]
    if not origins:
        return DataOrigin.MODEL
    # `ORIGIN_BY_RELIABILITY` est l'ordre canonique, du plus fiable au moins
    # fiable : la position la plus tardive est donc la plus faible.
    return max(origins, key=ORIGIN_BY_RELIABILITY.index)


ALL_TOOL_NAMES: tuple[str, ...] = (
    "analyze_shipment_risk",
    "calculate_irrigation_requirement",
    "create_recommendation",
    "get_shipment",
    "list_fields",
)
