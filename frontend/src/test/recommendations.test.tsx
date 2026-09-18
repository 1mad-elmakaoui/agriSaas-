/**
 * La boucle de décision à l'écran.
 *
 * Deux propriétés qui se dégraderaient en silence : un verdict ne se rend
 * qu'une fois, et un taux d'acceptation ne s'affiche jamais sans son
 * dénominateur.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RecommendationsPage } from '@/pages/RecommendationsPage'
import { RecordDecision } from '@/components/RecordDecision'
import { api } from '@/lib/api'
import type { RecommendationCounts, RecordedRecommendation } from '@/lib/types'

afterEach(() => vi.restoreAllMocks())

const pending: RecordedRecommendation = {
  id: 'r1',
  domain: 'IRRIGATION',
  domain_label_fr: 'Irrigation',
  subject_id: 'P03',
  subject_label_fr: 'Verger agrumes Est',
  headline_fr: 'Irrigation recommandée : 2 187 m³ sur 15h05.',
  outcome_code: 'IRRIGATE',
  rationale_fr: 'Déficit projeté au-dessus du seuil de déclenchement.',
  verdict: 'PENDING',
  verdict_label_fr: 'En attente de décision',
  decided_at: null,
  decided_by_name: null,
  decision_note_fr: null,
  created_at: '2026-09-17T06:00:00Z',
  provenance: {
    state: 'DERIVED',
    state_label_fr: 'Calculé',
    origin: 'SEED_DEMO',
    origin_label_fr: 'Jeu de démonstration',
    source_id: 'decision-engine',
    source_label_fr: 'Moteur de décision',
  },
}

const accepted: RecordedRecommendation = {
  ...pending,
  id: 'r2',
  subject_id: 'P01',
  subject_label_fr: 'Serre tomate Nord',
  verdict: 'ACCEPTED',
  verdict_label_fr: 'Acceptée',
  decided_at: '2026-09-17T08:00:00Z',
  decided_by_name: 'Nadia Benali',
  decision_note_fr: 'Tour d’eau lancé à 6 h.',
}

const counts: RecommendationCounts = {
  pending: 1,
  accepted: 1,
  rejected: 0,
  modified: 0,
  decided: 1,
  total: 2,
  acceptance_rate: 1,
  acceptance_label_fr: '100 % des décisions rendues',
}

async function open(rows: RecordedRecommendation[], tally = counts) {
  vi.spyOn(api, 'recommendations').mockResolvedValue(rows)
  vi.spyOn(api, 'recommendationCounts').mockResolvedValue(tally)
  render(
    <MemoryRouter>
      <RecommendationsPage />
    </MemoryRouter>,
  )
  await waitFor(() => expect(screen.getByText('Recommandations')).toBeInTheDocument())
}

describe('la liste', () => {
  it('sépare ce qui attend une décision de ce qui est décidé', async () => {
    await open([pending, accepted])
    await waitFor(() =>
      expect(screen.getByText('En attente de décision', { selector: 'h2' })).toBeInTheDocument(),
    )
    expect(screen.getByText('Décidées')).toBeInTheDocument()
  })

  it('n’offre un verdict que sur une recommandation en attente', async () => {
    // Permettre de « corriger » écraserait qui avait décidé quoi.
    await open([pending, accepted])
    await waitFor(() => expect(screen.getByText('Verger agrumes Est')).toBeInTheDocument())

    const waiting = screen.getByText('Verger agrumes Est').closest('div.rounded-lg')
    const settled = screen.getByText('Serre tomate Nord').closest('div.rounded-lg')
    expect(within(waiting as HTMLElement).getByRole('button', { name: 'Accepter' })).toBeInTheDocument()
    expect(
      within(settled as HTMLElement).queryByRole('button', { name: 'Accepter' }),
    ).not.toBeInTheDocument()
  })

  it('nomme qui a décidé et quand', async () => {
    await open([accepted])
    await waitFor(() => expect(screen.getByText(/Nadia Benali/)).toBeInTheDocument())
    expect(screen.getByText(/Tour d’eau lancé à 6 h/)).toBeInTheDocument()
  })

  it('porte la provenance de la recommandation archivée', async () => {
    await open([pending])
    await waitFor(() =>
      expect(screen.getByText('Jeu de démonstration')).toBeInTheDocument(),
    )
  })

  it('rend un verdict puis recharge', async () => {
    const decide = vi.spyOn(api, 'decideRecommendation').mockResolvedValue(accepted)
    await open([pending])
    await userEvent.click(screen.getByRole('button', { name: 'Accepter' }))
    expect(decide).toHaveBeenCalledWith('r1', 'ACCEPTED')
  })
})

describe('le taux d’acceptation', () => {
  it('ne s’affiche jamais sans son dénominateur', async () => {
    // « 50 % » seul se lit comme une performance ; « sur 2 décisions rendues »
    // se vérifie à la main depuis la liste juste en dessous.
    await open([pending, accepted])
    await waitFor(() => expect(screen.getByText('Taux d’acceptation')).toBeInTheDocument())
    expect(screen.getByText(/sur 1 décision\(s\) rendue\(s\)/)).toBeInTheDocument()
  })

  it('est absent plutôt que nul quand rien n’a été décidé', async () => {
    await open([pending], {
      ...counts,
      accepted: 0,
      decided: 0,
      total: 1,
      acceptance_rate: null,
      acceptance_label_fr: 'Aucune décision rendue pour l’instant.',
    })
    await waitFor(() => expect(screen.getByText('Taux d’acceptation')).toBeInTheDocument())
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.getByText(/Aucune décision rendue/)).toBeInTheDocument()
    expect(screen.queryByText('0 %')).not.toBeInTheDocument()
  })
})

describe('l’enregistrement d’une proposition', () => {
  it('n’envoie qu’un sujet, jamais une décision', async () => {
    // Accepter une décision venue du navigateur laisserait archiver n'importe
    // quel chiffre sous le nom du moteur.
    const record = vi.spyOn(api, 'recordRecommendation').mockResolvedValue(pending)
    render(<RecordDecision domain="IRRIGATION" subjectId="P03" />)
    await userEvent.click(
      screen.getByRole('button', { name: 'Enregistrer cette proposition' }),
    )
    expect(record).toHaveBeenCalledWith('IRRIGATION', 'P03')
  })

  it('dit qu’aucune action n’a été déclenchée', async () => {
    vi.spyOn(api, 'recordRecommendation').mockResolvedValue(pending)
    render(<RecordDecision domain="IRRIGATION" subjectId="P03" />)
    await userEvent.click(
      screen.getByRole('button', { name: 'Enregistrer cette proposition' }),
    )
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(
        /Aucune action n’a été déclenchée/,
      ),
    )
  })
})
