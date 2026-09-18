/**
 * The two panels, and the invariants they carry.
 *
 * These tests pin the behaviours that would degrade in silence: a value losing
 * its provenance chip, an absent figure rendering as a dash with no reason, a
 * typed crop name reaching the DOM as markup. None of these breaks a screen —
 * they just quietly make the product lie.
 */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { SourcesAndEvidence, WhyThisDecision } from '@/components/decision'
import { Figure, SourceChip } from '@/components/primitives'
import { HOSTILE_NAME, decision } from './fixtures'

describe('« Pourquoi cette décision ? »', () => {
  it('shows every available input with both provenance axes', () => {
    render(<WhyThisDecision decision={decision} />)
    const inputs = screen.getByRole('list', { name: 'Entrées du calcul' })
    const moisture = within(inputs).getByText('Humidité du sol').closest('li')
    expect(moisture).not.toBeNull()
    // Both axes, never collapsed: « Simulé » alone would not say whether the
    // number came from the demonstration dataset or an offline provider.
    expect(within(moisture as HTMLElement).getByText('Simulé')).toBeInTheDocument()
    expect(
      within(moisture as HTMLElement).getByText('Jeu de démonstration'),
    ).toBeInTheDocument()
  })

  it('replaces a missing input with its reason, never with a blank', () => {
    render(<WhyThisDecision decision={decision} />)
    const missing = screen.getByRole('list', { name: 'Entrées manquantes' })
    expect(within(missing).getByText('Tarif de l’eau')).toBeInTheDocument()
    expect(
      within(missing).getByText(/Aucun tarif de l’eau n’est renseigné/, { exact: false }),
    ).toBeInTheDocument()
  })

  it('numbers the calculation steps and keeps the FAO equation numbers', () => {
    render(<WhyThisDecision decision={decision} />)
    const steps = screen.getByText(/ET0 = 5,83/).closest('ol')
    expect(steps).not.toBeNull()
    expect((steps as HTMLElement).textContent).toContain('FAO-56 éq. 6')
    expect((steps as HTMLElement).textContent).toContain('FAO-56 éq. 31')
  })

  it('names the input that lowered the reliability, not just the score', () => {
    // « Fiabilité : Moyenne » alone tells the user nothing they can act on.
    render(<WhyThisDecision decision={decision} />)
    expect(screen.getByText(/Fiabilité : Moyenne/)).toBeInTheDocument()
    expect(
      screen.getByText(/Une mesure réelle relèverait la fiabilité/, { exact: false }),
    ).toBeInTheDocument()
  })

  it('states what could not be produced, with the reason', () => {
    render(<WhyThisDecision decision={decision} />)
    const quality = screen.getByText('Qualité des données').closest('section')
    expect(within(quality as HTMLElement).getByText(/Aucun tarif de l’eau/)).toBeInTheDocument()
  })

  it('keeps the tool trace behind a disclosure, closed by default', async () => {
    render(
      <WhyThisDecision
        decision={decision}
        toolCalls={[
          {
            name: 'calculate_irrigation_requirement',
            arguments: { field_code: 'P03' },
            succeeded: true,
            result: { water_volume_m3: 84.2 },
            error_fr: null,
          },
        ]}
      />,
    )
    expect(screen.queryByText('water_volume_m3')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /Voir les calculs/ }))
    expect(screen.getByText('water_volume_m3')).toBeInTheDocument()
  })
})

describe('« Sources et preuves »', () => {
  it('renders business language with the source named', () => {
    render(<SourcesAndEvidence decision={decision} />)
    expect(screen.getByText('Météo du jour')).toBeInTheDocument()
    expect(screen.getByText('Fournisseur hors ligne')).toBeInTheDocument()
  })

  it('says so when a decision carries no evidence', () => {
    render(<SourcesAndEvidence decision={{ ...decision, evidence: [] }} />)
    expect(screen.getByText(/Aucune preuve/)).toBeInTheDocument()
  })
})

describe('rendering values that came from a database row', () => {
  it('renders typed markup as text, never as an element', () => {
    const { container } = render(<Figure label="Produit" value={HOSTILE_NAME} />)
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText(HOSTILE_NAME)).toBeInTheDocument()
  })

  it('carries both axes on the chip itself, for a test to read', () => {
    const { container } = render(
      <SourceChip
        state="OBSERVED"
        stateLabelFr="Observé"
        origin="SENSOR"
        originLabelFr="Capteur"
      />,
    )
    const chip = container.querySelector('[data-state]')
    expect(chip?.getAttribute('data-state')).toBe('OBSERVED')
    expect(chip?.getAttribute('data-origin')).toBe('SENSOR')
  })
})

describe('an absent figure', () => {
  it('shows the reason instead of a dash', () => {
    render(
      <Figure
        label="Coût estimé"
        value={null}
        missingReason="Aucun tarif de l’eau n’est renseigné."
      />,
    )
    expect(screen.getByText('Aucun tarif de l’eau n’est renseigné.')).toBeInTheDocument()
    expect(screen.queryByText('—')).not.toBeInTheDocument()
  })

  it('never renders a zero in place of a missing value', () => {
    render(<Figure label="Coût estimé" value={null} />)
    expect(screen.queryByText('0')).not.toBeInTheDocument()
    expect(screen.getByText('Non disponible.')).toBeInTheDocument()
  })
})
