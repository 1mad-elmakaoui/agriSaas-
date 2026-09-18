/**
 * The shapes the API actually returns.
 *
 * Hand-written rather than generated, and deliberately *narrow*: every optional
 * field here is optional in the backend for a documented reason — no flow rate
 * means no duration, no water tariff means no cost. Typing them as `number`
 * with a zero default would erase the distinction the whole product exists to
 * preserve.
 */

/** Epistemic status: how do we know this? */
export type DataState = 'OBSERVED' | 'FORECAST' | 'DERIVED' | 'INFERRED' | 'SIMULATED'

/** Provenance: where did it physically come from? */
export type DataOrigin =
  | 'SENSOR'
  | 'MANUAL_ENTRY'
  | 'EXTERNAL_API'
  | 'SATELLITE'
  | 'REFERENCE_TABLE'
  | 'MODEL'
  | 'SEED_DEMO'

export type Recommendation = 'IRRIGATE' | 'MONITOR' | 'NO_IRRIGATION' | 'POSTPONE_RAIN'
export type StressLevel = 'normal' | 'moderate' | 'high' | 'critical'

export interface Moisture {
  value_pct: number
  recorded_at: string
  state: DataState
  state_label_fr: string
  origin: DataOrigin
  origin_label_fr: string
}

export interface Field {
  code: string
  name_fr: string
  site_name_fr: string
  area_ha: number
  latitude: number
  longitude: number
  boundary_geojson: unknown | null
  crop_code: string | null
  crop_name_fr: string | null
  soil_name_fr: string | null
  system_name_fr: string | null
  has_flow_rate: boolean
  has_water_tariff: boolean
  moisture: Moisture | null
  /** Set when no recommendation can be produced. The parcelle still renders. */
  blocked_reason_fr: string | null
}

export interface DecisionInput {
  key: string
  label_fr: string
  value: number | string | null
  unit: string | null
  state: DataState
  state_label_fr: string
  origin: DataOrigin
  origin_label_fr: string
  source_label_fr: string | null
  missing_reason_fr: string | null
}

export interface EvidenceItem {
  label_fr: string
  detail_fr: string
  state: DataState
  state_label_fr: string
  source_label_fr: string | null
  observed_at: string | null
}

export interface Decision {
  domain: string
  domain_label_fr: string
  subject_id: string
  subject_label_fr: string
  headline_fr: string
  outcome_code: string
  inputs: DecisionInput[]
  calculation_steps_fr: string[]
  assumptions_fr: string[]
  evidence: EvidenceItem[]
  tradeoffs_fr: string[]
  warnings_fr: string[]
  reliability: string | null
  reliability_label_fr: string | null
  unavailable_fr: Record<string, string>
}

export interface IrrigationRecommendation {
  field_code: string
  field_name_fr: string
  recommendation: Recommendation
  recommendation_label_fr: string
  headline_fr: string
  water_volume_m3: number
  net_requirement_mm: number
  duration_minutes: number | null
  duration_label_fr: string | null
  estimated_cost_mad: number | null
  et0_mm_day: number
  etc_mm_day: number
  stress_level: StressLevel
  stress_label_fr: string
  reliability: string | null
  reliability_label_fr: string | null
  decision: Decision
}

export interface Shipment {
  reference: string
  product_name_fr: string | null
  volume_tonnes: number
  transport_mode: string
  transport_mode_label_fr: string
  status: string
  status_label_fr: string
  origin_site_fr: string
  destination_site_fr: string
  origin_latitude: number
  origin_longitude: number
  destination_latitude: number
  destination_longitude: number
  departure_at: string
  sla_deadline_at: string
  hours_to_deadline: number
  requires_cold_chain: boolean | null
  source_field_code: string | null
  /** What the platform cannot yet say about this shipment. Server-supplied. */
  risk_analysis_fr: string | null
}

export interface Overview {
  tenant_name_fr: string
  is_demo: boolean
  fields_total: number
  fields_to_irrigate: number
  fields_blocked: number
  shipments_in_transit: number
  shipments_due_within_24h: number
  not_delivered_fr: string[]
}

export interface ToolCall {
  name: string
  arguments: Record<string, unknown>
  succeeded: boolean
  result: Record<string, unknown>
  error_fr: string | null
}

export interface CopilotAnswer {
  text_fr: string
  tool_calls: ToolCall[]
  iterations: number
  truncated: boolean
  stop_reason: string
  model: string
  prompt_version: string
  run_id: string | null
  cost_usd: number | null
  cost_label_fr: string
}

export interface Session {
  access_token: string
  role: string
  role_label_fr: string
}

/** The three-field error shape every endpoint returns. */
export interface ApiError {
  code: string
  message_fr: string
  remedy_fr: string | null
  run_id: string | null
}

// ---------------------------------------------------------------------------
// Risque logistique
// ---------------------------------------------------------------------------
export type RiskLevel = 'LOW' | 'MODERATE' | 'HIGH' | 'CRITICAL'

export interface ExposedSegment {
  from_name_fr: string
  to_name_fr: string
  road_ref: string
  distance_km: number
  /** Heures après le départ. C'est ce qui distingue « la route sera coupée »
   *  de « le camion y sera au mauvais moment ». */
  hours_from_departure: number
  entry_at: string
  exit_at: string
  risk_level: RiskLevel
  risk_level_label_fr: string
  reasons_fr: string[]
  latitudes: number[]
  longitudes: number[]
}

export interface TransportAlternative {
  id: string
  label_fr: string
  description_fr: string
  kind: string
  is_current_plan: boolean
  is_feasible: boolean
  rank: number | null
  is_recommended: boolean
  /** `null` sur une option écartée : la renseigner la ferait figurer dans le
   *  tableau comparatif comme un choix possible. */
  cost_mad: number | null
  cost_delta_mad: number | null
  duration_hours: number | null
  duration_delta_hours: number | null
  departure_at: string | null
  arrival_at: string | null
  sla_margin_hours: number | null
  risk_level: RiskLevel | null
  risk_level_label_fr: string | null
  risk_delta: number | null
  exposure_fraction: number | null
  rejection_reasons_fr: string[]
  path_lonlat: number[][]
}

export interface ShipmentRisk {
  reference: string
  headline_fr: string
  outcome_code: string
  profile_fr: string
  profile_rationale_fr: string
  risk_level: RiskLevel
  risk_level_label_fr: string
  exposure_fraction: number
  exposed_distance_km: number
  disruption_indicator: number
  /** À afficher **avec** l'indicateur, toujours. */
  disruption_caveat_fr: string
  departure_at: string
  sla_deadline_at: string
  sla_compliant: boolean
  exposed_segments: ExposedSegment[]
  alternatives: TransportAlternative[]
  reliability: string | null
  reliability_label_fr: string | null
  decision: Decision
}

// ---------------------------------------------------------------------------
// Analyse en langage naturel
// ---------------------------------------------------------------------------
export type AttemptStatus =
  | 'ok'
  | 'invalid'
  | 'plan_error'
  | 'too_expensive'
  | 'execution_error'
  | 'duplicate'
  | 'refused'

export interface Attempt {
  index: number
  /** Rendu même sur un échec : sans la requête, un échec ne se conteste pas. */
  sql: string
  status: AttemptStatus
  issues_fr: string[]
  plan_cost: number | null
  duration_ms: number | null
}

export interface Analysis {
  question: string
  text_fr: string
  /** `null` quand aucune requête n'a abouti. */
  sql: string | null
  columns: string[]
  rows: Record<string, unknown>[]
  row_count: number
  truncated: boolean
  truncation_reason_fr: string | null
  attempts: Attempt[]
  refused: boolean
  refusal_reason_fr: string | null
  prompt_version: string
  run_id: string | null
  cost_usd: number | null
  cost_label_fr: string
}

// ---------------------------------------------------------------------------
// Recommandations
// ---------------------------------------------------------------------------
export type Verdict = 'PENDING' | 'ACCEPTED' | 'REJECTED' | 'MODIFIED'
export type DecidableVerdict = Exclude<Verdict, 'PENDING'>

/**
 * Une recommandation **archivée**.
 *
 * Nommée ainsi pour ne pas se confondre avec `Recommendation`, qui est le code
 * de sortie du moteur d'irrigation (`IRRIGATE`, `MONITOR`…). Les deux mots
 * existent dans le produit et désignent des choses différentes : l'un est un
 * verdict de moteur, l'autre une ligne en base portant un verdict humain.
 */
export interface RecordedRecommendation {
  id: string
  domain: string
  domain_label_fr: string
  subject_id: string
  subject_label_fr: string
  headline_fr: string
  outcome_code: string
  rationale_fr: string | null
  verdict: Verdict
  verdict_label_fr: string
  decided_at: string | null
  /** `null` tant que personne n'a décidé — jamais « système ». */
  decided_by_name: string | null
  decision_note_fr: string | null
  created_at: string
  provenance: {
    state: DataState
    state_label_fr: string
    origin: DataOrigin
    origin_label_fr: string
    source_id: string
    source_label_fr: string
  }
}

export interface RecommendationCounts {
  pending: number
  accepted: number
  rejected: number
  modified: number
  decided: number
  total: number
  /** `null` tant que rien n'a été décidé : 0 % se lirait « tout est refusé ». */
  acceptance_rate: number | null
  acceptance_label_fr: string
}

// ---------------------------------------------------------------------------
// Abonnement, quotas et usage
// ---------------------------------------------------------------------------
/**
 * Une jauge, avec son dénominateur.
 *
 * `limit: null` signifie **illimité**, jamais zéro : un zéro se lirait comme une
 * interdiction totale. `fraction` vaut `null` dans ce cas — une jauge sans
 * dénominateur ne se dessine pas, et en dessiner une pleine ou une vide serait
 * également faux.
 */
export interface Quota {
  label_fr: string
  used: number
  limit: number | null
  remaining: number | null
  unit_fr: string
  fraction: number | null
  allowed: boolean
  message_fr: string | null
  remedy_fr: string | null
}

export interface Plan {
  code: string
  name_fr: string
  description_fr: string
  max_fields: number | null
  max_shipments: number | null
  max_agent_messages_per_month: number | null
  max_analytics_queries_per_month: number | null
  max_llm_spend_usd_per_month: number | null
}

export interface Subscription {
  plan: Plan
  available_plans: Plan[]
  period_start: string
  fields: Quota
  shipments: Quota
  agent_messages: Quota
  analytics_queries: Quota
  llm_spend: Quota
  /** Vrai dès qu'un appel du mois n'est pas tarifé : la dépense est minorante. */
  spend_is_partial: boolean
  spend_notice_fr: string
}

// ---------------------------------------------------------------------------
// Organisation, journal et conformité
// ---------------------------------------------------------------------------
export type UserRole =
  | 'ADMIN'
  | 'SUPPLY_CHAIN_MANAGER'
  | 'OPERATIONS_MANAGER'
  | 'AGRONOME'
  | 'ANALYST'
  | 'EXECUTIVE'

export interface SignupDraft {
  organisation_name: string
  full_name: string
  email: string
  password: string
}

export interface MemberDraft {
  email: string
  full_name: string
  role: UserRole
  password: string
}

export interface Member {
  id: string
  email: string
  full_name: string
  role: UserRole
  role_label_fr: string
  is_active: boolean
  created_at: string
}

export interface Organisation {
  id: string
  name: string
  slug: string
  region_code: string | null
  plan_code: string
  is_demo: boolean
  members: Member[]
}

export interface AuditEntry {
  occurred_at: string
  /** `null` pour un acte sans utilisateur identifié, ou un compte supprimé. */
  actor_email: string | null
  action: string
  resource_type: string
  resource_id: string | null
  outcome: string
  run_id: string | null
  detail: Record<string, unknown> | null
}

export interface Compliance {
  /** `null` = non déclarée. Jamais « Maroc » par défaut. */
  residency_country: string | null
  residency_provider: string | null
  cndp_declaration_number: string | null
  audit_retention_days: number
  retention_enforced: boolean
  statements_fr: string[]
  limitations_fr: string[]
}

// ---------------------------------------------------------------------------
// Premier parcours
// ---------------------------------------------------------------------------
export interface OnboardingStep {
  key: string
  title_fr: string
  detail_fr: string
  done: boolean
  /** `null` quand l'étape est franchie : une étape faite n'a pas d'action. */
  action_fr: string | null
}

export interface OnboardingState {
  steps: OnboardingStep[]
  complete: boolean
  first_field_code: string | null
}

export interface ReferenceChoice {
  code: string
  name_fr: string
  /** Vrai pour une ligne posée par l'organisation, qui masque la ligne globale. */
  is_local: boolean
}

export interface OnboardingChoices {
  sites: ReferenceChoice[]
  crops: ReferenceChoice[]
  soils: ReferenceChoice[]
  irrigation_systems: ReferenceChoice[]
}

export interface FieldDraft {
  code: string
  name_fr: string
  site_code: string
  area_ha: number
  latitude: number
  longitude: number
  crop_code: string
  soil_code: string
  irrigation_system_code: string
}

export interface MoistureReading {
  field_code: string
  value_pct: number
  depth_cm: number | null
  recorded_at: string
  state: DataState
  state_label_fr: string
  origin: DataOrigin
  origin_label_fr: string
}
