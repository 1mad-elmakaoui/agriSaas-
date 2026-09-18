"""Schémas d'entrée et de sortie de l'API.

Deux invariants portés par le schéma plutôt que par la relecture :

1. **Aucun schéma d'entrée n'expose `data_state` ni `data_origin`.** Un client
   ne peut pas déclarer qu'une saisie manuelle vient d'un capteur ; le serveur
   décide. `extra="forbid"` fait que l'envoyer quand même est une erreur 422 et
   non un champ ignoré en silence.
2. **Tout schéma de sortie portant une valeur porte sa provenance.** Le couple
   voyage jusqu'au pixel ; une puce de source dans l'interface le lit, elle ne
   le devine pas.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.domain.enums import (
    DataOrigin,
    DataState,
    ReliabilityLevel,
    SiteType,
    UserRole,
)

__all__ = [
    "AlternativeSchema",
    "AnalysisSchema",
    "AttemptSchema",
    "ErrorResponse",
    "ExposedSegmentSchema",
    "FieldCreate",
    "FieldOut",
    "IrrigationOut",
    "LoginRequest",
    "MoistureCreate",
    "MoistureOut",
    "OverviewOut",
    "PlanChangeIn",
    "PlanOut",
    "ProvenanceOut",
    "QuotaOut",
    "RecommendationCountsOut",
    "RecommendationIn",
    "RecommendationOut",
    "ShipmentOut",
    "ShipmentRiskSchema",
    "SiteCreate",
    "SiteOut",
    "SubscriptionOut",
    "TenantOut",
    "TokenResponse",
    "UserOut",
    "VerdictIn",
]


class _In(BaseModel):
    """Base des schémas d'entrée : tout champ inconnu est refusé.

    `extra="forbid"` est ce qui donne son mordant à l'invariant 1 : un client
    qui envoie `data_origin` reçoit une erreur explicite plutôt qu'un silence
    qui laisserait croire que la valeur a été prise en compte.
    """

    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class LoginRequest(_In):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class SignupRequest(_In):
    """Inscription autonome d'une organisation.

    Le mot de passe est contraint en **longueur** et rien d'autre : une règle de
    composition — une majuscule, un chiffre, un caractère spécial — produit des
    mots de passe courts et prévisibles, et ne vérifie pas ce qui compte. Ce que
    nous ne faisons pas non plus est vérifier l'adresse contre une liste de
    mots de passe compromis ; c'est une absence, pas une garantie tacite.
    """

    organisation_name: str = Field(min_length=2, max_length=200)
    full_name: str = Field(min_length=2, max_length=200)
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    region_code: str | None = Field(default=None, max_length=40)


class MemberCreate(_In):
    """Création d'un membre par un administrateur.

    Aucun courriel n'est envoyé : la plateforme n'a pas d'acheminement de
    courrier. Le mot de passe provisoire est donc rendu à l'administrateur, qui
    le transmet par un autre canal — et la réponse le dit, plutôt que de laisser
    croire qu'une invitation est partie.
    """

    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    role: UserRole
    password: str = Field(min_length=12, max_length=256)


class TokenResponse(_Out):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - the OAuth scheme name, not a secret
    expires_in_minutes: int
    role: str
    role_label_fr: str
    tenant_name: str
    is_demo: bool


class ProvenanceOut(_Out):
    """Le couple de provenance, tel que l'interface le reçoit.

    Les libellés français accompagnent les valeurs stockées : le composant
    d'affichage ne doit pas porter sa propre table de traduction, sinon deux
    écrans finissent par nommer différemment le même état.
    """

    state: DataState
    state_label_fr: str
    origin: DataOrigin
    origin_label_fr: str
    source_id: str
    source_label_fr: str


class TenantOut(_Out):
    id: UUID
    name: str
    slug: str
    region_code: str | None
    plan_code: str
    is_demo: bool
    #: Présent uniquement pour un tenant de démonstration : l'interface affiche
    #: le bandeau à partir de ce champ, elle ne le déduit pas du nom.
    demo_notice_fr: str | None = None


class UserOut(_Out):
    id: UUID
    email: str
    full_name: str
    role: str
    role_label_fr: str
    is_active: bool


class SiteCreate(_In):
    code: str = Field(min_length=1, max_length=40)
    name_fr: str = Field(min_length=1, max_length=200)
    site_type: SiteType
    region_code: str | None = Field(default=None, max_length=40)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    elevation_m: float | None = None
    capacity_tonnes: float | None = Field(default=None, ge=0)
    has_cold_storage: bool = False
    boundary_geojson: dict[str, Any] | None = None


class SiteOut(_Out):
    id: UUID
    code: str
    name_fr: str
    site_type: SiteType
    site_type_label_fr: str
    region_code: str | None
    latitude: float
    longitude: float
    elevation_m: float | None
    capacity_tonnes: float | None
    has_cold_storage: bool
    boundary_geojson: dict[str, Any] | None
    created_at: datetime
    #: Provenance de l'enregistrement. Une fiche de site saisie par un
    #: exploitant et une fiche issue du jeu de démonstration ne doivent pas
    #: pouvoir être confondues à l'écran.
    provenance: ProvenanceOut
    reliability: ReliabilityLevel | None = None
    reliability_label_fr: str | None = None


class ErrorResponse(_Out):
    code: str
    message_fr: str
    remedy_fr: str | None = None


# ---------------------------------------------------------------------------
# Parcelles
# ---------------------------------------------------------------------------
class MoistureOut(_Out):
    """Dernière mesure d'humidité, avec sa provenance.

    Nullable en bloc : sans mesure, l'interface affiche « Aucune mesure » plutôt
    qu'un zéro qui se lirait comme un sol sec.
    """

    value_pct: float
    recorded_at: datetime
    state: DataState
    state_label_fr: str
    origin: DataOrigin
    origin_label_fr: str


class FieldOut(_Out):
    code: str
    name_fr: str
    site_name_fr: str
    area_ha: float
    latitude: float
    longitude: float
    boundary_geojson: dict[str, Any] | None
    crop_code: str | None
    crop_name_fr: str | None
    soil_name_fr: str | None
    system_name_fr: str | None
    has_flow_rate: bool
    has_water_tariff: bool
    moisture: MoistureOut | None
    #: Motif empêchant toute recommandation. La parcelle reste affichée, en gris,
    #: avec ce texte : une parcelle disparue d'un écran est indiscernable d'une
    #: parcelle qui va bien.
    blocked_reason_fr: str | None


class IrrigationOut(_Out):
    """La recommandation complète, telle que les deux panneaux la lisent.

    `decision` porte le contrat commun (`Decision.to_dict()`) : entrées avec
    provenance, étapes numérotées, hypothèses, preuves, indisponibilités. Les
    champs de tête sont les mêmes valeurs, remontées pour que la carte de
    recommandation n'ait pas à fouiller.
    """

    field_code: str
    field_name_fr: str
    recommendation: str
    recommendation_label_fr: str
    headline_fr: str
    water_volume_m3: float
    net_requirement_mm: float
    duration_minutes: float | None
    duration_label_fr: str | None
    estimated_cost_mad: float | None
    et0_mm_day: float
    etc_mm_day: float
    stress_level: str
    stress_label_fr: str
    reliability: ReliabilityLevel | None
    reliability_label_fr: str | None
    decision: dict[str, Any]


# ---------------------------------------------------------------------------
# Expéditions
# ---------------------------------------------------------------------------
class ShipmentOut(_Out):
    reference: str
    product_name_fr: str | None
    volume_tonnes: float
    transport_mode: str
    transport_mode_label_fr: str
    status: str
    status_label_fr: str
    origin_site_fr: str
    destination_site_fr: str
    origin_latitude: float
    origin_longitude: float
    destination_latitude: float
    destination_longitude: float
    departure_at: datetime
    sla_deadline_at: datetime
    hours_to_deadline: float
    requires_cold_chain: bool | None
    source_field_code: str | None
    #: Renseigné quand une capacité manque encore pour cette expédition. Rendu
    #: par l'API plutôt qu'écrit en dur dans l'interface : le jour où le moteur
    #: correspondant arrive, l'écran cesse de l'annoncer sans être modifié.
    risk_analysis_fr: str | None


# ---------------------------------------------------------------------------
# Vue générale
# ---------------------------------------------------------------------------
class OverviewOut(_Out):
    """Le décompte de la page d'accueil.

    Aucun score composite : chaque nombre est dénombrable et vérifiable à la
    main depuis les autres écrans. Un indice de santé global serait un chiffre
    que personne ne peut contester.
    """

    tenant_name_fr: str
    is_demo: bool
    fields_total: int
    fields_to_irrigate: int
    fields_blocked: int
    shipments_in_transit: int
    shipments_due_within_24h: int
    #: Capacités déclarées non livrées, nommées pour l'écran d'accueil.
    not_delivered_fr: list[str]



# ---------------------------------------------------------------------------
# Risque logistique
# ---------------------------------------------------------------------------
class ExposedSegmentSchema(_Out):
    """Un tronçon exposé, avec l'heure à laquelle le véhicule y passe.

    L'heure n'est pas décorative : c'est elle qui distingue « la route sera
    coupée » de « le camion y sera au mauvais moment ».
    """

    from_name_fr: str
    to_name_fr: str
    road_ref: str
    distance_km: float
    hours_from_departure: float
    entry_at: datetime
    exit_at: datetime
    risk_level: str
    risk_level_label_fr: str
    reasons_fr: list[str]
    latitudes: list[float]
    longitudes: list[float]


class AlternativeSchema(_Out):
    """Une option examinée.

    Une option écartée porte `null` sur chaque chiffre. Les renseigner la ferait
    figurer dans un tableau comparatif comme un choix possible.
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
    cost_delta_mad: float | None
    duration_hours: float | None
    duration_delta_hours: float | None
    departure_at: datetime | None
    arrival_at: datetime | None
    sla_margin_hours: float | None
    risk_level: str | None
    risk_level_label_fr: str | None
    risk_delta: float | None
    exposure_fraction: float | None
    rejection_reasons_fr: list[str]
    path_lonlat: list[list[float]]


class ShipmentRiskSchema(_Out):
    reference: str
    headline_fr: str
    outcome_code: str
    profile_fr: str
    profile_rationale_fr: str
    risk_level: str
    risk_level_label_fr: str
    exposure_fraction: float
    exposed_distance_km: float
    disruption_indicator: float
    #: À afficher **avec** l'indicateur, toujours.
    disruption_caveat_fr: str
    departure_at: datetime
    sla_deadline_at: datetime
    sla_compliant: bool
    exposed_segments: list[ExposedSegmentSchema]
    alternatives: list[AlternativeSchema]
    reliability: ReliabilityLevel | None
    reliability_label_fr: str | None
    decision: dict[str, Any]


# ---------------------------------------------------------------------------
# Analyse en langage naturel
# ---------------------------------------------------------------------------
class AttemptSchema(_Out):
    """Une tentative, réussie ou non.

    Les tentatives ratées sont rendues **avec leur SQL**. C'est la matière de
    l'enquête quand une question ne trouve pas sa réponse, et un exploitant qui
    ne voit qu'un échec sans requête n'a rien à contester.
    """

    index: int
    sql: str
    status: str
    issues_fr: list[str]
    plan_cost: float | None
    duration_ms: int | None


class AnalysisSchema(_Out):
    question: str
    text_fr: str
    #: `null` quand aucune requête n'a abouti — jamais la dernière tentative
    #: ratée présentée comme si elle avait tourné.
    sql: str | None
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    truncation_reason_fr: str | None
    attempts: list[AttemptSchema]
    refused: bool
    refusal_reason_fr: str | None
    prompt_version: str
    run_id: str | None
    cost_usd: float | None
    cost_label_fr: str


# ---------------------------------------------------------------------------
# Recommandations
# ---------------------------------------------------------------------------
class RecommendationOut(_Out):
    id: UUID
    domain: str
    domain_label_fr: str
    subject_id: str
    subject_label_fr: str
    headline_fr: str
    outcome_code: str
    rationale_fr: str | None
    verdict: str
    verdict_label_fr: str
    decided_at: datetime | None
    #: Qui a décidé. `null` tant que personne ne l'a fait — jamais « système ».
    decided_by_name: str | None
    decision_note_fr: str | None
    created_at: datetime
    provenance: ProvenanceOut


class RecommendationIn(_In):
    """Enregistrer une proposition depuis l'interface.

    Le sujet et le domaine, rien de plus : la décision est **recalculée** par le
    moteur au moment de l'enregistrement, jamais envoyée par le client. Accepter
    une décision du navigateur reviendrait à laisser n'importe qui archiver le
    chiffre de son choix sous le nom du moteur.
    """

    domain: Literal["IRRIGATION", "LOGISTICS"]
    subject_id: str = Field(min_length=1, max_length=120)
    rationale_fr: str | None = Field(default=None, max_length=2000)


class VerdictIn(_In):
    """Le verdict humain.

    `PENDING` n'est pas acceptable en entrée : c'est l'état initial, pas une
    décision. L'accepter laisserait « annuler ma décision » passer pour une
    opération, alors qu'elle effacerait la trace de qui avait décidé quoi.
    """

    verdict: Literal["ACCEPTED", "REJECTED", "MODIFIED"]
    note_fr: str | None = Field(default=None, max_length=2000)


class RecommendationCountsOut(_Out):
    pending: int
    accepted: int
    rejected: int
    modified: int
    decided: int
    total: int
    #: `null` tant que rien n'a été décidé : 0 % sur zéro décision se lirait
    #: comme « tout est refusé ».
    acceptance_rate: float | None
    acceptance_label_fr: str


# ---------------------------------------------------------------------------
# Saisie : parcelle et mesure d'humidité
# ---------------------------------------------------------------------------
class FieldCreate(_In):
    """Création d'une parcelle.

    Aucun champ de provenance : le serveur écrit `MANUAL_ENTRY`, toujours. Un
    client ne peut pas déclarer qu'une saisie au clavier vient d'un capteur.
    """

    code: str = Field(min_length=1, max_length=40)
    name_fr: str = Field(min_length=1, max_length=200)
    site_code: str = Field(min_length=1, max_length=40)
    area_ha: float = Field(gt=0, le=100_000)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    crop_code: str = Field(min_length=1, max_length=40)
    soil_code: str = Field(min_length=1, max_length=40)
    irrigation_system_code: str = Field(min_length=1, max_length=40)
    planting_date: date | None = None
    flow_rate_m3_per_hour: float | None = Field(default=None, gt=0)
    water_cost_per_m3: float | None = Field(default=None, ge=0)
    #: Le tracé dessiné sur la carte. Facultatif : une parcelle sans contour
    #: reste exploitable — c'est la surface déclarée qui entre dans la dose,
    #: jamais l'aire du polygone.
    boundary_geojson: dict[str, Any] | None = None


class MoistureCreate(_In):
    """Relevé d'humidité du sol, saisi à la main.

    `depth_cm` est facultative et le reste : une mesure sans profondeur déclarée
    est moins exploitable qu'une mesure avec, et inventer 30 cm produirait un
    bilan hydrique faux et crédible.
    """

    value_pct: float = Field(ge=0, le=100)
    depth_cm: float | None = Field(default=None, gt=0, le=500)
    recorded_at: datetime | None = None


class MoistureReadingOut(_Out):
    """Le relevé tel qu'il a été écrit, provenance comprise.

    Rendu en retour de création pour que l'interface affiche la provenance
    décidée par le serveur au lieu de celle qu'elle croyait envoyer.
    """

    field_code: str
    value_pct: float
    depth_cm: float | None
    recorded_at: datetime
    state: DataState
    state_label_fr: str
    origin: DataOrigin
    origin_label_fr: str


class ReferenceChoiceOut(_Out):
    code: str
    name_fr: str
    #: Vrai pour une ligne posée par l'organisation, qui masque la ligne
    #: globale de même code. L'interface le signale : deux valeurs de même nom
    #: n'engagent pas la même chose.
    is_local: bool


class OnboardingChoicesOut(_Out):
    """Ce que le formulaire de première parcelle peut proposer."""

    sites: list[ReferenceChoiceOut]
    crops: list[ReferenceChoiceOut]
    soils: list[ReferenceChoiceOut]
    irrigation_systems: list[ReferenceChoiceOut]


class OnboardingStepOut(_Out):
    key: str
    title_fr: str
    detail_fr: str
    done: bool
    #: `null` quand l'étape est franchie. Une étape faite n'a pas d'action.
    action_fr: str | None


class OnboardingStateOut(_Out):
    steps: list[OnboardingStepOut]
    complete: bool
    #: Où emmener l'utilisateur quand une parcelle existe déjà.
    first_field_code: str | None


# ---------------------------------------------------------------------------
# Organisation, conformité et journal d'accès
# ---------------------------------------------------------------------------
class MemberOut(_Out):
    id: UUID
    email: str
    full_name: str
    role: UserRole
    role_label_fr: str
    is_active: bool
    created_at: datetime


class OrganisationOut(_Out):
    id: UUID
    name: str
    slug: str
    region_code: str | None
    plan_code: str
    #: Une organisation de démonstration porte des données fabriquées. Le dire
    #: partout où elle apparaît est une règle du produit, pas une politesse.
    is_demo: bool
    members: list[MemberOut]


class RoleChangeIn(_In):
    role: UserRole


class AuditEntryOut(_Out):
    occurred_at: datetime
    #: `null` pour un évènement sans utilisateur identifié — une connexion
    #: refusée, ou l'acte d'un compte depuis supprimé.
    actor_email: str | None
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    run_id: str | None
    detail: dict[str, Any] | None


class ComplianceOut(_Out):
    """Ce que la plateforme déclare, et ce qu'elle ne déclare pas.

    Chaque champ nullable l'est pour la même raison : le code ne peut pas
    vérifier où tourne sa base ni si une déclaration a été déposée. Non
    renseigné, il rend `null`, et l'interface affiche « non déclarée » — jamais
    une valeur plausible.
    """

    residency_country: str | None
    residency_provider: str | None
    cndp_declaration_number: str | None
    audit_retention_days: int
    #: Vrai seulement quand une tâche purge effectivement les lignes échues.
    #: Faux tant que la durée n'est qu'annoncée.
    retention_enforced: bool
    statements_fr: list[str]
    limitations_fr: list[str]


class DeletionRequestIn(_In):
    """Suppression d'une organisation.

    La confirmation est l'identifiant lisible, recopié à la main. Une case à
    cocher se coche par réflexe ; un nom se recopie en ayant lu ce qu'on tape.
    """

    confirmation: str = Field(min_length=1, max_length=80)


class DeletionReceiptOut(_Out):
    organisation_id: UUID
    deleted_rows: dict[str, int]
    total_rows: int
    message_fr: str


# ---------------------------------------------------------------------------
# Abonnement et usage
# ---------------------------------------------------------------------------
class QuotaOut(_Out):
    label_fr: str
    used: float
    #: `null` = illimité, jamais zéro : un zéro se lirait comme une interdiction.
    limit: float | None
    remaining: float | None
    unit_fr: str
    #: Part consommée, pour une jauge. `null` si illimité.
    fraction: float | None
    allowed: bool
    message_fr: str | None
    remedy_fr: str | None


class PlanOut(_Out):
    code: str
    name_fr: str
    description_fr: str
    max_fields: int | None
    max_shipments: int | None
    max_agent_messages_per_month: int | None
    max_analytics_queries_per_month: int | None
    max_llm_spend_usd_per_month: float | None


class SubscriptionOut(_Out):
    plan: PlanOut
    available_plans: list[PlanOut]
    period_start: datetime
    fields: QuotaOut
    shipments: QuotaOut
    agent_messages: QuotaOut
    analytics_queries: QuotaOut
    llm_spend: QuotaOut
    #: `true` dès qu'un appel du mois n'est pas tarifé : la dépense affichée est
    #: alors un **minorant**, et l'écran le dit.
    spend_is_partial: bool
    spend_notice_fr: str


class PlanChangeIn(_In):
    """Changement de plan, par un administrateur.

    Aucun paiement n'est traité dans ce périmètre (§6) : un plan est provisionné,
    pas acheté. Le champ existe pour que le changement laisse une trace d'audit
    nommant qui l'a fait.
    """

    plan_code: str = Field(min_length=1, max_length=40)
