/**
 * The single door to the backend.
 *
 * Every call goes through `request`, so the three-field French error shape is
 * decoded in exactly one place. A component that catches an error gets
 * `message_fr` and `remedy_fr` — never a status code it would have to translate
 * itself, and never a stack trace.
 */
import type {
  Analysis,
  ApiError,
  AuditEntry,
  Compliance,
  DecidableVerdict,
  CopilotAnswer,
  Field,
  FieldDraft,
  IrrigationRecommendation,
  MemberDraft,
  MoistureReading,
  OnboardingChoices,
  OnboardingState,
  Organisation,
  Overview,
  RecommendationCounts,
  RecordedRecommendation,
  Session,
  SignupDraft,
  Shipment,
  ShipmentRisk,
  Subscription,
  UserRole,
} from './types'

const TOKEN_KEY = 'atlasagri.token'

export class RequestFailed extends Error {
  readonly code: string
  readonly remedyFr: string | null
  readonly status: number

  constructor(status: number, payload: Partial<ApiError>) {
    super(payload.message_fr ?? 'Une erreur inattendue est survenue.')
    this.name = 'RequestFailed'
    this.status = status
    this.code = payload.code ?? 'unknown'
    this.remedyFr = payload.remedy_fr ?? null
  }
}

export function storedToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    // Private browsing, or storage disabled. Not an error: the user signs in
    // again. Failing loudly here would block a working session.
    return null
  }
}

export function storeToken(token: string | null): void {
  try {
    if (token === null) localStorage.removeItem(TOKEN_KEY)
    else localStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* see storedToken */
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = storedToken()
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init.headers ?? {}),
    },
  })

  if (!response.ok) {
    let payload: Partial<ApiError> = {}
    try {
      payload = (await response.json()) as Partial<ApiError>
    } catch {
      // A non-JSON body means the failure happened before our handler ran —
      // a proxy, a crash. Say that, rather than showing raw HTML.
      payload = {
        code: 'unreachable',
        message_fr: 'Le serveur est momentanément injoignable.',
        remedy_fr: 'Réessayez dans quelques instants.',
      }
    }
    throw new RequestFailed(response.status, payload)
  }

  return (await response.json()) as T
}

export const api = {
  login: (email: string, password: string) =>
    request<Session>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    }),
  signup: (draft: SignupDraft) =>
    request<Session>('/auth/inscription', {
      method: 'POST',
      body: JSON.stringify(draft),
    }),
  overview: () => request<Overview>('/vue-generale'),
  fields: () => request<Field[]>('/parcelles'),
  irrigation: (code: string) =>
    request<IrrigationRecommendation>(`/parcelles/${encodeURIComponent(code)}/irrigation`),
  shipments: () => request<Shipment[]>('/expeditions'),
  shipment: (reference: string) =>
    request<Shipment>(`/expeditions/${encodeURIComponent(reference)}`),
  shipmentRisk: (reference: string) =>
    request<ShipmentRisk>(`/expeditions/${encodeURIComponent(reference)}/risque`),
  analyse: (question: string) =>
    request<Analysis>('/analyse', {
      method: 'POST',
      body: JSON.stringify({ question }),
    }),
  recommendations: () => request<RecordedRecommendation[]>('/recommandations'),
  recommendationCounts: () => request<RecommendationCounts>('/recommandations/decompte'),
  recordRecommendation: (domain: string, subjectId: string, rationaleFr?: string) =>
    request<RecordedRecommendation>('/recommandations', {
      method: 'POST',
      body: JSON.stringify({
        domain,
        subject_id: subjectId,
        rationale_fr: rationaleFr ?? null,
      }),
    }),
  decideRecommendation: (id: string, verdict: DecidableVerdict, noteFr?: string) =>
    request<RecordedRecommendation>(`/recommandations/${encodeURIComponent(id)}/verdict`, {
      method: 'POST',
      body: JSON.stringify({ verdict, note_fr: noteFr ?? null }),
    }),
  // -- premier parcours ---------------------------------------------------
  onboarding: () => request<OnboardingState>('/demarrage'),
  onboardingChoices: () => request<OnboardingChoices>('/demarrage/choix'),
  createField: (draft: FieldDraft) =>
    request<Field>('/parcelles', { method: 'POST', body: JSON.stringify(draft) }),
  // Aucune provenance envoyée : le serveur écrit « saisie manuelle », toujours.
  // Un client ne peut pas déclarer que sa frappe vient d'un capteur, et le
  // schéma d'entrée refuse le champ plutôt que de l'ignorer.
  recordMoisture: (fieldCode: string, valuePct: number, depthCm?: number) =>
    request<MoistureReading>(
      `/parcelles/${encodeURIComponent(fieldCode)}/humidite`,
      {
        method: 'POST',
        body: JSON.stringify({ value_pct: valuePct, depth_cm: depthCm ?? null }),
      },
    ),

  // -- abonnement ----------------------------------------------------------
  subscription: () => request<Subscription>('/abonnement'),
  changePlan: (planCode: string) =>
    request<Subscription>('/abonnement/plan', {
      method: 'POST',
      body: JSON.stringify({ plan_code: planCode }),
    }),

  // -- organisation et conformité -----------------------------------------
  organisation: () => request<Organisation>('/organisation'),
  addMember: (draft: MemberDraft) =>
    request<Organisation>('/organisation/membres', {
      method: 'POST',
      body: JSON.stringify(draft),
    }),
  setMemberRole: (userId: string, role: UserRole) =>
    request<Organisation>(`/organisation/membres/${encodeURIComponent(userId)}/role`, {
      method: 'POST',
      body: JSON.stringify({ role }),
    }),
  deleteMember: (userId: string) =>
    request<{ message_fr: string }>(
      `/organisation/membres/${encodeURIComponent(userId)}`,
      { method: 'DELETE' },
    ),
  auditJournal: (limit = 100) =>
    request<AuditEntry[]>(`/organisation/journal?limit=${limit}`),
  exportData: () => request<Record<string, unknown>>('/organisation/export'),
  compliance: () => request<Compliance>('/organisation/conformite'),
  deleteOrganisation: (confirmation: string) =>
    request<{ total_rows: number; message_fr: string }>('/organisation', {
      method: 'DELETE',
      body: JSON.stringify({ confirmation }),
    }),

  ask: (question: string) =>
    request<CopilotAnswer>('/copilote', {
      method: 'POST',
      body: JSON.stringify({ question }),
    }),
}
