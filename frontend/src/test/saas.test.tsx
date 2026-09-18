/**
 * Abonnement, administration, démarrage.
 *
 * Trois écrans, et les mêmes trois règles qui les traversent :
 *
 * * une jauge affiche **toujours** son dénominateur, et l'illimité n'en dessine
 *   aucune — une barre vide se lirait « rien de consommé », une barre pleine
 *   « plafond atteint », et les deux seraient faux ;
 * * un refus vient du serveur, mot pour mot. L'interface ne rédige pas ses
 *   propres messages d'erreur ;
 * * ce qui n'est pas déclaré s'affiche « non déclarée », jamais rempli par une
 *   valeur plausible.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AdminPage } from '@/pages/AdminPage'
import { SignIn } from '@/pages/SignIn'
import { OnboardingPage } from '@/pages/OnboardingPage'
import { SubscriptionPage } from '@/pages/SubscriptionPage'
import { QuotaGauge } from '@/components/QuotaGauge'
import { RequestFailed, api } from '@/lib/api'
import type {
  Compliance,
  OnboardingChoices,
  OnboardingState,
  Organisation,
  Quota,
  Subscription,
} from '@/lib/types'

afterEach(() => vi.restoreAllMocks())

function quota(overrides: Partial<Quota> = {}): Quota {
  return {
    label_fr: 'Questions au copilote ce mois-ci',
    used: 87,
    limit: 100,
    remaining: 13,
    unit_fr: 'questions',
    fraction: 0.87,
    allowed: true,
    message_fr: null,
    remedy_fr: null,
    ...overrides,
  }
}

const subscription: Subscription = {
  plan: {
    code: 'COOPERATIVE',
    name_fr: 'Coopérative',
    description_fr: 'Pour une coopérative de quelques exploitations.',
    max_fields: 25,
    max_shipments: 10,
    max_agent_messages_per_month: 100,
    max_analytics_queries_per_month: 50,
    max_llm_spend_usd_per_month: 5,
  },
  available_plans: [
    {
      code: 'COOPERATIVE',
      name_fr: 'Coopérative',
      description_fr: 'Pour une coopérative de quelques exploitations.',
      max_fields: 25,
      max_shipments: 10,
      max_agent_messages_per_month: 100,
      max_analytics_queries_per_month: 50,
      max_llm_spend_usd_per_month: 5,
    },
    {
      code: 'ENTREPRISE',
      name_fr: 'Entreprise',
      description_fr: 'Sans plafond.',
      max_fields: null,
      max_shipments: null,
      max_agent_messages_per_month: null,
      max_analytics_queries_per_month: null,
      max_llm_spend_usd_per_month: 500,
    },
  ],
  period_start: '2026-09-01T00:00:00Z',
  fields: quota({ label_fr: 'Parcelles suivies', used: 7, limit: 25, unit_fr: 'parcelles' }),
  shipments: quota({
    label_fr: 'Expéditions enregistrées',
    used: 1,
    limit: 10,
    unit_fr: 'expéditions',
  }),
  agent_messages: quota(),
  analytics_queries: quota({
    label_fr: "Questions d'analyse ce mois-ci",
    used: 2,
    limit: 50,
  }),
  llm_spend: quota({
    label_fr: 'Dépense du modèle ce mois-ci',
    used: 0.42,
    limit: 5,
    unit_fr: 'USD',
    fraction: 0.084,
  }),
  spend_is_partial: false,
  spend_notice_fr:
    "Dépense dérivée d'une grille tarifaire publiée, non rapprochée d'une facture.",
}

const organisation: Organisation = {
  id: 'org-1',
  name: 'Souss Primeurs',
  slug: 'souss-primeurs',
  region_code: 'SOUSS_MASSA',
  plan_code: 'COOPERATIVE',
  is_demo: true,
  members: [
    {
      id: 'u1',
      email: 'nadia@example.ma',
      full_name: 'Nadia Benali',
      role: 'ADMIN',
      role_label_fr: 'Administrateur',
      is_active: true,
      created_at: '2026-01-05T09:00:00Z',
    },
  ],
}

const compliance: Compliance = {
  residency_country: null,
  residency_provider: null,
  cndp_declaration_number: null,
  audit_retention_days: 1095,
  retention_enforced: false,
  statements_fr: ["La localisation des données n'est pas déclarée par cette installation."],
  limitations_fr: [
    "La durée de conservation est annoncée mais n'est appliquée par aucune purge automatique.",
  ],
}

const onboarding: OnboardingState = {
  complete: false,
  first_field_code: null,
  steps: [
    {
      key: 'site',
      title_fr: 'Déclarer une exploitation',
      detail_fr: 'Le lieu auquel vos parcelles se rattachent.',
      done: true,
      action_fr: null,
    },
    {
      key: 'field',
      title_fr: 'Créer une première parcelle',
      detail_fr: 'Surface, position, culture, sol et système.',
      done: false,
      action_fr: 'Ajoutez une parcelle.',
    },
  ],
}

const choices: OnboardingChoices = {
  sites: [{ code: 'F01', name_fr: 'Ferme du Souss', is_local: true }],
  crops: [
    { code: 'TOMATE', name_fr: 'Tomate', is_local: false },
    { code: 'AGRUME', name_fr: 'Agrume calibré localement', is_local: true },
  ],
  soils: [{ code: 'LIMON', name_fr: 'Limon', is_local: false }],
  irrigation_systems: [{ code: 'GOUTTE', name_fr: 'Goutte-à-goutte', is_local: false }],
}

// ---------------------------------------------------------------------------
// La jauge
// ---------------------------------------------------------------------------
describe('QuotaGauge', () => {
  it('affiche toujours son dénominateur', () => {
    render(<QuotaGauge quota={quota()} />)
    expect(screen.getByText(/sur 100 questions/)).toBeInTheDocument()
  })

  it("ne dessine aucune barre quand il n'y a pas de plafond", () => {
    render(<QuotaGauge quota={quota({ limit: null, remaining: null, fraction: null })} />)
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.getByText(/sans plafond/)).toBeInTheDocument()
  })

  it('rend le message de refus du serveur, et son remède', () => {
    render(
      <QuotaGauge
        quota={quota({
          used: 100,
          remaining: 0,
          fraction: 1,
          allowed: false,
          message_fr: 'Plafond atteint : questions au copilote, 100 sur 100.',
          remedy_fr: 'Le compteur repart au début du mois prochain.',
        })}
      />,
    )
    expect(screen.getByText(/Plafond atteint/)).toBeInTheDocument()
    expect(screen.getByText(/mois prochain/)).toBeInTheDocument()
  })

  it("accorde l'unité en nombre, et laisse une abréviation intacte", () => {
    const { rerender } = render(
      <QuotaGauge
        quota={quota({
          label_fr: 'Expéditions enregistrées',
          used: 1,
          limit: 10,
          unit_fr: 'expéditions',
          fraction: 0.1,
        })}
      />,
    )
    expect(screen.getByText('1 expédition')).toBeInTheDocument()
    expect(screen.getByText(/sur 10 expéditions/)).toBeInTheDocument()

    rerender(
      <QuotaGauge quota={quota({ used: 1, limit: 5, unit_fr: 'USD', fraction: 0.2 })} />,
    )
    expect(screen.getByText('1,00 USD')).toBeInTheDocument()
  })

  it("écrit un décompte sans décimale et une dépense avec deux", () => {
    const { rerender } = render(<QuotaGauge quota={quota({ used: 100, limit: 100 })} />)
    expect(screen.getByText('100 questions')).toBeInTheDocument()
    rerender(
      <QuotaGauge
        quota={quota({ used: 0.4, limit: 5, unit_fr: 'USD', fraction: 0.08 })}
      />,
    )
    expect(screen.getByText('0,40 USD')).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Abonnement
// ---------------------------------------------------------------------------
describe('Abonnement', () => {
  it('montre les cinq jauges et dit que la dépense est dérivée', async () => {
    vi.spyOn(api, 'subscription').mockResolvedValue(subscription)
    render(<SubscriptionPage />)

    await waitFor(() => expect(screen.getByText(/Parcelles suivies/)).toBeInTheDocument())
    expect(screen.getAllByRole('progressbar')).toHaveLength(5)
    expect(screen.getByText(/non rapprochée d'une facture/)).toBeInTheDocument()
  })

  it("écrit « Illimité » plutôt qu'un zéro sur un plan sans plafond", async () => {
    vi.spyOn(api, 'subscription').mockResolvedValue(subscription)
    render(<SubscriptionPage />)

    await waitFor(() => expect(screen.getByText('Entreprise')).toBeInTheDocument())
    expect(screen.getAllByText('Illimité').length).toBeGreaterThan(0)
  })

  it('relaie le refus du serveur quand on n’est pas administrateur', async () => {
    vi.spyOn(api, 'subscription').mockResolvedValue(subscription)
    vi.spyOn(api, 'changePlan').mockRejectedValue(
      new RequestFailed(403, {
        code: 'forbidden',
        message_fr: "Seul un administrateur peut changer le plan de l'organisation.",
        remedy_fr: 'Demandez à un administrateur de votre organisation.',
      }),
    )
    render(<SubscriptionPage />)

    await waitFor(() => expect(screen.getByText('Entreprise')).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: 'Choisir ce plan' }))

    expect(
      await screen.findByText("Seul un administrateur peut changer le plan de l'organisation."),
    ).toBeInTheDocument()
    expect(screen.getByText('Demandez à un administrateur de votre organisation.')).toBeInTheDocument()
  })

  it('ne propose pas de changer pour le plan déjà en cours', async () => {
    vi.spyOn(api, 'subscription').mockResolvedValue(subscription)
    render(<SubscriptionPage />)

    await waitFor(() => expect(screen.getByText('Plan actuel')).toBeInTheDocument())
    expect(screen.getAllByRole('button', { name: 'Choisir ce plan' })).toHaveLength(1)
  })
})

// ---------------------------------------------------------------------------
// Administration
// ---------------------------------------------------------------------------
describe('Administration', () => {
  function mockAdmin() {
    vi.spyOn(api, 'organisation').mockResolvedValue(organisation)
    vi.spyOn(api, 'compliance').mockResolvedValue(compliance)
    vi.spyOn(api, 'auditJournal').mockResolvedValue([
      {
        occurred_at: '2026-09-17T08:00:00Z',
        actor_email: 'nadia@example.ma',
        action: 'audit:read',
        resource_type: 'AUDIT_LOG',
        resource_id: null,
        outcome: 'SUCCESS',
        run_id: 'abc',
        detail: null,
      },
      {
        occurred_at: '2026-09-17T07:00:00Z',
        actor_email: null,
        action: 'auth:login',
        resource_type: 'SESSION',
        resource_id: null,
        outcome: 'DENIED',
        run_id: 'def',
        detail: null,
      },
    ])
  }

  it('affiche les membres avec leur rôle', async () => {
    mockAdmin()
    render(<AdminPage />)
    await waitFor(() => expect(screen.getByText('Nadia Benali')).toBeInTheDocument())
    expect(screen.getByLabelText('Rôle de Nadia Benali')).toHaveValue('ADMIN')
  })

  it("écrit « non déclarée » plutôt que d'inventer un pays", async () => {
    mockAdmin()
    render(<AdminPage />)
    await waitFor(() => expect(screen.getByText('Non déclarée')).toBeInTheDocument())
    expect(screen.queryByText('Maroc')).not.toBeInTheDocument()
  })

  it('dit qu’une conservation annoncée n’est pas appliquée', async () => {
    mockAdmin()
    render(<AdminPage />)
    await waitFor(() =>
      expect(screen.getByText(/annoncée, non appliquée/)).toBeInTheDocument(),
    )
  })

  it("montre ce qui n'est pas couvert, à côté de ce qui l'est", async () => {
    mockAdmin()
    render(<AdminPage />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Ce qui n'est pas couvert/ })).toBeInTheDocument(),
    )
    await userEvent.click(screen.getByRole('button', { name: /Ce qui n'est pas couvert/ }))
    expect(screen.getByText(/aucune purge automatique/)).toBeInTheDocument()
  })

  it('écrit « — » pour un acte sans auteur identifié, jamais « système »', async () => {
    mockAdmin()
    render(<AdminPage />)
    await waitFor(() => expect(screen.getByText('auth:login')).toBeInTheDocument())
    const row = screen.getByText('auth:login').closest('tr')
    expect(row).not.toBeNull()
    expect(within(row as HTMLElement).getByText('—')).toBeInTheDocument()
  })

  it("dit qu'aucun courriel n'est envoyé quand on ajoute un membre", async () => {
    mockAdmin()
    const add = vi.spyOn(api, 'addMember').mockResolvedValue(organisation)
    render(<AdminPage />)

    await waitFor(() => expect(screen.getByLabelText('Nom complet')).toBeInTheDocument())
    expect(screen.getByText(/Aucun courriel n'est envoyé/)).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText('Adresse électronique'), 'ali@example.ma')
    await userEvent.type(screen.getByLabelText('Nom complet'), 'Ali Bennani')
    await userEvent.selectOptions(screen.getByLabelText('Rôle'), 'ANALYST')
    await userEvent.type(
      screen.getByLabelText('Mot de passe provisoire'),
      'un-mot-de-passe-assez-long',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Ajouter le membre' }))

    await waitFor(() => expect(add).toHaveBeenCalledTimes(1))
    expect(add.mock.calls[0]?.[0]).toMatchObject({
      email: 'ali@example.ma',
      role: 'ANALYST',
    })
  })

  it("exige de recopier l'identifiant avant de pouvoir supprimer", async () => {
    mockAdmin()
    render(<AdminPage />)
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /Supprimer définitivement l'organisation/ }),
      ).toBeInTheDocument(),
    )
    await userEvent.click(
      screen.getByRole('button', { name: /Supprimer définitivement l'organisation/ }),
    )

    const confirm = screen.getByRole('button', { name: 'Supprimer définitivement' })
    expect(confirm).toBeDisabled()
    await userEvent.type(screen.getByLabelText(/Recopiez l'identifiant/), 'souss-primeurs')
    expect(confirm).toBeEnabled()
  })
})

// ---------------------------------------------------------------------------
// Démarrage
// ---------------------------------------------------------------------------
describe('Démarrage', () => {
  function mockOnboarding(state: OnboardingState = onboarding) {
    vi.spyOn(api, 'onboarding').mockResolvedValue(state)
    vi.spyOn(api, 'onboardingChoices').mockResolvedValue(choices)
  }

  it('rend les étapes du serveur, et leur action tant qu’elles restent à faire', async () => {
    mockOnboarding()
    render(
      <MemoryRouter>
        <OnboardingPage />
      </MemoryRouter>,
    )
    await waitFor(() =>
      expect(screen.getByText('Créer une première parcelle')).toBeInTheDocument(),
    )
    expect(screen.getByText('Ajoutez une parcelle.')).toBeInTheDocument()
    // L'étape franchie n'a pas d'action : rien n'est inventé à la place.
    const done = screen.getByText('Déclarer une exploitation').closest('li')
    expect(within(done as HTMLElement).queryByText(/Ajoutez/)).not.toBeInTheDocument()
  })

  it('signale une valeur de référentiel propre à l’organisation', async () => {
    mockOnboarding()
    render(
      <MemoryRouter>
        <OnboardingPage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByLabelText('Culture')).toBeInTheDocument())
    expect(
      screen.getByRole('option', { name: /Agrume calibré localement \(valeur de votre organisation\)/ }),
    ).toBeInTheDocument()
  })

  it("ne demande ni débit ni tarif de l'eau", async () => {
    mockOnboarding()
    render(
      <MemoryRouter>
        <OnboardingPage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByLabelText('Culture')).toBeInTheDocument())
    expect(screen.queryByLabelText(/Débit/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Tarif/i)).not.toBeInTheDocument()
  })

  it('crée la parcelle puis propose le relevé, sans champ de provenance', async () => {
    mockOnboarding()
    const create = vi.spyOn(api, 'createField').mockResolvedValue({
      code: 'N01',
    } as never)
    render(
      <MemoryRouter>
        <OnboardingPage />
      </MemoryRouter>,
    )

    await waitFor(() => expect(screen.getByLabelText('Culture')).toBeInTheDocument())
    await userEvent.type(screen.getByLabelText('Code de la parcelle'), 'N01')
    await userEvent.type(screen.getByLabelText('Nom'), 'Parcelle du bas')
    await userEvent.selectOptions(screen.getByLabelText("Site d'exploitation"), 'F01')
    await userEvent.type(screen.getByLabelText('Surface (ha)'), '2.5')
    await userEvent.type(screen.getByLabelText('Latitude'), '30.42')
    await userEvent.type(screen.getByLabelText('Longitude'), '-9.58')
    await userEvent.selectOptions(screen.getByLabelText('Culture'), 'TOMATE')
    await userEvent.selectOptions(screen.getByLabelText('Sol'), 'LIMON')
    await userEvent.selectOptions(screen.getByLabelText("Système d'irrigation"), 'GOUTTE')
    await userEvent.click(screen.getByRole('button', { name: 'Créer la parcelle' }))

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1))
    const draft = create.mock.calls[0]?.[0] as unknown as Record<string, unknown>
    expect(draft.crop_code).toBe('TOMATE')
    expect(Object.keys(draft)).not.toContain('data_origin')
    expect(Object.keys(draft)).not.toContain('data_state')

    expect(
      await screen.findByText(/Saisir un relevé d'humidité — N01/),
    ).toBeInTheDocument()
  })

  it('relaie le plafond du plan quand la création est refusée', async () => {
    mockOnboarding()
    vi.spyOn(api, 'createField').mockRejectedValue(
      new RequestFailed(429, {
        code: 'quota_exceeded',
        message_fr:
          'Plafond atteint : parcelles suivies, 25 parcelles sur 25 pour le plan « Coopérative ».',
        remedy_fr: 'Demandez un plan supérieur à votre administrateur.',
      }),
    )
    render(
      <MemoryRouter>
        <OnboardingPage />
      </MemoryRouter>,
    )

    await waitFor(() => expect(screen.getByLabelText('Culture')).toBeInTheDocument())
    await userEvent.type(screen.getByLabelText('Code de la parcelle'), 'N01')
    await userEvent.type(screen.getByLabelText('Nom'), 'Parcelle du bas')
    await userEvent.selectOptions(screen.getByLabelText("Site d'exploitation"), 'F01')
    await userEvent.type(screen.getByLabelText('Surface (ha)'), '2.5')
    await userEvent.type(screen.getByLabelText('Latitude'), '30.42')
    await userEvent.type(screen.getByLabelText('Longitude'), '-9.58')
    await userEvent.selectOptions(screen.getByLabelText('Culture'), 'TOMATE')
    await userEvent.selectOptions(screen.getByLabelText('Sol'), 'LIMON')
    await userEvent.selectOptions(screen.getByLabelText("Système d'irrigation"), 'GOUTTE')
    await userEvent.click(screen.getByRole('button', { name: 'Créer la parcelle' }))

    expect(await screen.findByText(/Plafond atteint/)).toBeInTheDocument()
    expect(
      screen.getByText('Demandez un plan supérieur à votre administrateur.'),
    ).toBeInTheDocument()
  })

  it("dit qu'il faut un site quand l'organisation n'en a aucun", async () => {
    mockOnboarding()
    vi.spyOn(api, 'onboardingChoices').mockResolvedValue({ ...choices, sites: [] })
    render(
      <MemoryRouter>
        <OnboardingPage />
      </MemoryRouter>,
    )
    await waitFor(() =>
      expect(screen.getByText(/Aucun site d'exploitation n'existe encore/)).toBeInTheDocument(),
    )
    expect(screen.queryByLabelText('Culture')).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Inscription
// ---------------------------------------------------------------------------
describe('Inscription', () => {
  function renderSignIn() {
    render(
      <MemoryRouter>
        <SignIn onSignedIn={() => {}} />
      </MemoryRouter>,
    )
  }

  it("ne demande pas le nom de l'organisation sur l'onglet de connexion", () => {
    renderSignIn()
    expect(screen.queryByLabelText("Nom de l'organisation")).not.toBeInTheDocument()
  })

  it("crée l'organisation et dit qu'aucun paiement n'est demandé", async () => {
    const signup = vi.spyOn(api, 'signup').mockResolvedValue({
      access_token: 'jeton',
      role: 'ADMIN',
      role_label_fr: 'Administrateur',
    })
    renderSignIn()

    await userEvent.click(screen.getByRole('tab', { name: 'Créer une organisation' }))
    expect(screen.getByText(/Aucun moyen\s+de paiement n'est demandé/)).toBeInTheDocument()

    await userEvent.type(screen.getByLabelText("Nom de l'organisation"), 'Coopérative Aït Melloul')
    await userEvent.type(screen.getByLabelText('Votre nom'), 'Rachid Amrani')
    await userEvent.type(screen.getByLabelText('Adresse électronique'), 'rachid@example.ma')
    await userEvent.type(screen.getByLabelText('Mot de passe'), 'un-mot-de-passe-assez-long')
    await userEvent.click(screen.getByRole('button', { name: "Créer l'organisation" }))

    await waitFor(() => expect(signup).toHaveBeenCalledTimes(1))
    expect(signup.mock.calls[0]?.[0]).toMatchObject({
      organisation_name: 'Coopérative Aït Melloul',
      email: 'rachid@example.ma',
    })
  })

  it("relaie « ce courriel est déjà rattaché » du serveur", async () => {
    vi.spyOn(api, 'signup').mockRejectedValue(
      new RequestFailed(422, {
        code: 'invalid_input',
        message_fr: 'Ce courriel est déjà rattaché à une organisation.',
        remedy_fr: 'Connectez-vous, ou inscrivez-vous avec une autre adresse.',
      }),
    )
    renderSignIn()
    await userEvent.click(screen.getByRole('tab', { name: 'Créer une organisation' }))
    await userEvent.type(screen.getByLabelText("Nom de l'organisation"), 'Une coopérative')
    await userEvent.type(screen.getByLabelText('Votre nom'), 'Quelqu’un')
    await userEvent.type(screen.getByLabelText('Adresse électronique'), 'pris@example.ma')
    await userEvent.type(screen.getByLabelText('Mot de passe'), 'un-mot-de-passe-assez-long')
    await userEvent.click(screen.getByRole('button', { name: "Créer l'organisation" }))

    expect(
      await screen.findByText('Ce courriel est déjà rattaché à une organisation.'),
    ).toBeInTheDocument()
  })
})
