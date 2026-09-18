/**
 * Page-level behaviour that the product depends on.
 *
 * Chiefly: the copilot must never present the model's prose as the answer, and
 * an undelivered capability must be visible as a statement rather than as an
 * empty panel that reads like a loading state.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CopilotPage } from '@/pages/CopilotPage'
import { ShipmentsPage } from '@/pages/ShipmentsPage'
import { OverviewPage } from '@/pages/OverviewPage'
import { api } from '@/lib/api'
import { HOSTILE_NAME, recommendation, shipment, shipmentRisk } from './fixtures'

afterEach(() => vi.restoreAllMocks())

const IRRIGATION_TOOL_RESULT = {
  field_code: 'P03',
  recommendation: 'IRRIGATE',
  recommendation_label_fr: 'Irrigation recommandée',
  headline_fr: recommendation.headline_fr,
  water_volume_m3: 84.2,
  net_requirement_mm: 12.4,
  duration_minutes: 130,
  estimated_cost_mad: null,
  et0_mm_day: 5.83,
  etc_mm_day: 4.08,
  reliability_label_fr: 'Moyenne',
  inputs: recommendation.decision.inputs,
  calculation_steps_fr: recommendation.decision.calculation_steps_fr,
  assumptions_fr: recommendation.decision.assumptions_fr,
  warnings_fr: [],
  unavailable_fr: { cout: 'Aucun tarif de l’eau n’est renseigné pour cette parcelle.' },
}

describe('Copilote', () => {
  it('renders the engine result as the answer, and the model prose as commentary', async () => {
    vi.spyOn(api, 'ask').mockResolvedValue({
      // The model claims a different number in prose. The screen must show the
      // engine's, and must not let the model's stand as the answer.
      text_fr: 'Il faut environ 300 m³ pour cette parcelle.',
      tool_calls: [
        {
          name: 'calculate_irrigation_requirement',
          arguments: { field_code: 'P03' },
          succeeded: true,
          result: IRRIGATION_TOOL_RESULT,
          error_fr: null,
        },
      ],
      iterations: 2,
      truncated: false,
      stop_reason: 'end_turn',
      model: 'fake',
      prompt_version: 'test',
      run_id: 'r',
      cost_usd: null,
      cost_label_fr: 'Coût inconnu : au moins un appel n’est pas tarifé.',
    })

    render(
      <MemoryRouter>
        <CopilotPage />
      </MemoryRouter>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Est-ce que je dois irriguer P03 ?' }))

    // The figure that reaches the user is the engine's.
    await waitFor(() => expect(screen.getByText('84 m³')).toBeInTheDocument())

    // The model's sentence is present but explicitly subordinate.
    const commentary = screen.getByText(/300 m³/).closest('section')
    expect(commentary).not.toBeNull()
    expect(
      within(commentary as HTMLElement).getByText(/les chiffres font foi ci-dessus/i),
    ).toBeInTheDocument()
  })

  it('says plainly when the copilot consulted no engine at all', async () => {
    vi.spyOn(api, 'ask').mockResolvedValue({
      text_fr: 'Bonjour, comment puis-je aider ?',
      tool_calls: [],
      iterations: 1,
      truncated: false,
      stop_reason: 'end_turn',
      model: 'fake',
      prompt_version: 'test',
      run_id: null,
      cost_usd: null,
      cost_label_fr: 'Coût inconnu.',
    })
    render(
      <MemoryRouter>
        <CopilotPage />
      </MemoryRouter>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Est-ce que je dois irriguer P03 ?' }))
    await waitFor(() =>
      expect(screen.getByText(/n’a consulté aucun moteur de calcul/)).toBeInTheDocument(),
    )
  })

  it('marks a truncated analysis instead of presenting it as complete', async () => {
    vi.spyOn(api, 'ask').mockResolvedValue({
      text_fr: 'Analyse interrompue.',
      tool_calls: [],
      iterations: 8,
      truncated: true,
      stop_reason: 'max_iterations',
      model: 'fake',
      prompt_version: 'test',
      run_id: null,
      cost_usd: null,
      cost_label_fr: 'Coût inconnu.',
    })
    render(
      <MemoryRouter>
        <CopilotPage />
      </MemoryRouter>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Est-ce que je dois irriguer P03 ?' }))
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/n’a pas pu être menée à son terme/),
    )
  })

  it('reports a failed tool as a failure, substituting nothing', async () => {
    vi.spyOn(api, 'ask').mockResolvedValue({
      text_fr: '',
      tool_calls: [
        {
          name: 'get_shipment',
          arguments: { reference: 'EXP-0000' },
          succeeded: false,
          result: { erreur: 'Expédition « EXP-0000 » introuvable.' },
          error_fr: 'Expédition « EXP-0000 » introuvable.',
        },
      ],
      iterations: 2,
      truncated: false,
      stop_reason: 'end_turn',
      model: 'fake',
      prompt_version: 'test',
      run_id: null,
      cost_usd: null,
      cost_label_fr: 'Coût inconnu.',
    })
    render(
      <MemoryRouter>
        <CopilotPage />
      </MemoryRouter>,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Est-ce que je dois irriguer P03 ?' }))
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/introuvable/),
    )
    expect(screen.getByText(/Aucune valeur n’a été substituée/)).toBeInTheDocument()
  })
})

describe('Expéditions', () => {
  it('loads the risk analysis separately from the sheet', async () => {
    // La fiche s'affiche immédiatement ; l'analyse fait tourner le moteur sur
    // trois itinéraires et arrive ensuite, avec son propre état de chargement.
    vi.spyOn(api, 'shipments').mockResolvedValue([shipment])
    vi.spyOn(api, 'shipmentRisk').mockResolvedValue(shipmentRisk)
    render(
      <MemoryRouter>
        <ShipmentsPage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('EXP-1842')).toBeInTheDocument())
    await waitFor(() =>
      expect(screen.getByText(/via Safi, El Jadida pour 669 MAD/)).toBeInTheDocument(),
    )
    expect(screen.getByText('Options examinées')).toBeInTheDocument()
  })

  it('still states an absence when the server declares one', async () => {
    // Le champ reste : la prochaine capacité manquante s'y logera, et l'écran
    // n'a pas à savoir laquelle.
    vi.spyOn(api, 'shipments').mockResolvedValue([
      { ...shipment, risk_analysis_fr: 'Le moteur X n’est pas livré.' },
    ])
    render(
      <MemoryRouter>
        <ShipmentsPage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getByText('EXP-1842')).toBeInTheDocument())
    const analysis = screen.getByText('Analyse de risque').closest('section')
    expect(within(analysis as HTMLElement).getByText(/n’est pas livré/)).toBeInTheDocument()
  })

  it('renders a product name typed by a user as text', async () => {
    vi.spyOn(api, 'shipments').mockResolvedValue([shipment])
    const { container } = render(
      <MemoryRouter>
        <ShipmentsPage />
      </MemoryRouter>,
    )
    await waitFor(() => expect(screen.getAllByText(HOSTILE_NAME).length).toBeGreaterThan(0))
    expect(container.querySelector('img')).toBeNull()
  })
})

describe('Vue générale', () => {
  it('lists what the installation does not do, from the server', async () => {
    vi.spyOn(api, 'overview').mockResolvedValue({
      tenant_name_fr: 'Souss Primeurs',
      is_demo: true,
      fields_total: 7,
      fields_to_irrigate: 3,
      fields_blocked: 1,
      shipments_in_transit: 1,
      shipments_due_within_24h: 1,
      not_delivered_fr: ['Analyse de risque logistique (moteur non livré)'],
    })
    render(
      <MemoryRouter>
        <OverviewPage />
      </MemoryRouter>,
    )
    await waitFor(() =>
      expect(screen.getByText(/Analyse de risque logistique/)).toBeInTheDocument(),
    )
    expect(screen.getByText('Parcelles sans recommandation')).toBeInTheDocument()
  })
})
