/**
 * La page d'analyse.
 *
 * Deux propriétés qui se dégraderaient en silence : les lignes doivent primer
 * sur la prose du modèle, et une tentative ratée doit montrer son SQL. La
 * seconde paraît un détail de journalisation ; c'est en fait la seule chose qui
 * rende un échec contestable par la personne qui connaît ses données.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AnalysisPage } from '@/pages/AnalysisPage'
import { api } from '@/lib/api'
import type { Analysis } from '@/lib/types'
import { HOSTILE_NAME } from './fixtures'

afterEach(() => vi.restoreAllMocks())

const base: Analysis = {
  question: 'Quelle est la surface totale par culture ?',
  text_fr: 'Les agrumes couvrent 4,1 ha et les tomates 3,5 ha.',
  sql: 'SELECT f.crop_name_fr, sum(f.area_ha) AS surface_ha FROM analytics.v_fields f GROUP BY f.crop_name_fr LIMIT 500',
  columns: ['crop_name_fr', 'surface_ha'],
  rows: [
    { crop_name_fr: 'Agrumes', surface_ha: 4.1 },
    { crop_name_fr: HOSTILE_NAME, surface_ha: 3.5 },
  ],
  row_count: 2,
  truncated: false,
  truncation_reason_fr: null,
  attempts: [
    { index: 1, sql: 'SELECT …', status: 'ok', issues_fr: [], plan_cost: 12.4, duration_ms: 6 },
  ],
  refused: false,
  refusal_reason_fr: null,
  prompt_version: 'test',
  run_id: 'r',
  cost_usd: null,
  cost_label_fr: 'Coût inconnu.',
}

async function ask(analysis: Analysis) {
  vi.spyOn(api, 'analyse').mockResolvedValue(analysis)
  render(
    <MemoryRouter>
      <AnalysisPage />
    </MemoryRouter>,
  )
  await userEvent.click(
    screen.getByRole('button', { name: 'Quelle est la surface totale par culture ?' }),
  )
}

describe('le résultat', () => {
  it('affiche les lignes avant la lecture faite par le modèle', async () => {
    await ask(base)
    await waitFor(() => expect(screen.getByText('Agrumes')).toBeInTheDocument())

    const result = screen.getByText('Résultat').closest('section')
    const reading = screen.getByText('Lecture du résultat').closest('section')
    expect(result).not.toBeNull()
    expect(reading).not.toBeNull()
    // Le tableau précède la prose dans le document.
    expect(
      (result as HTMLElement).compareDocumentPosition(reading as HTMLElement) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it('rend une valeur de base de données comme du texte', async () => {
    const { container } = { container: document.body }
    await ask(base)
    await waitFor(() => expect(screen.getByText(HOSTILE_NAME)).toBeInTheDocument())
    expect(container.querySelector('img')).toBeNull()
  })

  it('dit qu’un résultat vide est vide, sans l’expliquer', async () => {
    await ask({ ...base, rows: [], row_count: 0, text_fr: 'Aucune ligne.' })
    await waitFor(() =>
      expect(screen.getByText(/n’a renvoyé aucune ligne/)).toBeInTheDocument(),
    )
    expect(screen.getByText(/Ce n’est pas une erreur/)).toBeInTheDocument()
  })

  it('marque un résultat partiel plutôt que de le présenter comme complet', async () => {
    await ask({
      ...base,
      truncated: true,
      truncation_reason_fr: 'Résultat limité à 2 ligne(s) par la plateforme : il peut en exister davantage.',
    })
    await waitFor(() => expect(screen.getByText('Résultat partiel')).toBeInTheDocument())
    expect(screen.getByText(/peut en exister davantage/)).toBeInTheDocument()
  })
})

describe('la trace des tentatives', () => {
  it('reste repliée quand la première requête a abouti', async () => {
    await ask(base)
    await waitFor(() => expect(screen.getByText('Agrumes')).toBeInTheDocument())
    expect(screen.queryByText(/SELECT …/)).not.toBeInTheDocument()
  })

  it('s’ouvre d’elle-même quand il a fallu reprendre', async () => {
    // C'est exactement le moment où quelqu'un veut savoir ce qui a été tenté.
    await ask({
      ...base,
      attempts: [
        {
          index: 1,
          sql: 'SELECT f.chiffre_affaires FROM analytics.v_fields f',
          status: 'invalid',
          issues_fr: ['Colonne « chiffre_affaires » inconnue sur analytics.v_fields.'],
          plan_cost: null,
          duration_ms: null,
        },
        { index: 2, sql: 'SELECT f.code FROM analytics.v_fields f', status: 'ok', issues_fr: [], plan_cost: 3, duration_ms: 4 },
      ],
    })
    await waitFor(() =>
      expect(screen.getByText('Rejetée à la validation')).toBeInTheDocument(),
    )
    // Le SQL rejeté et l'erreur exacte sont tous deux là : l'un sans l'autre ne
    // permettrait pas de dire « la colonne s'appelle autrement ».
    const shown = screen.getAllByText(/chiffre_affaires/)
    expect(shown.length).toBeGreaterThanOrEqual(2)
  })
})

describe('un refus', () => {
  it('dit qu’aucune donnée n’a été lue, et montre la requête refusée', async () => {
    await ask({
      ...base,
      refused: true,
      refusal_reason_fr:
        'La relation « app.shipments » est hors de la surface analytique.',
      text_fr:
        'Cette question a produit une requête que la plateforme refuse d’exécuter. Aucune donnée n’a été lue.',
      sql: null,
      rows: [],
      row_count: 0,
      attempts: [
        {
          index: 1,
          sql: 'SELECT s.reference FROM app.shipments s',
          status: 'refused',
          issues_fr: ['La relation « app.shipments » est hors de la surface analytique.'],
          plan_cost: null,
          duration_ms: null,
        },
      ],
    })
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent(/Aucune donnée n’a été lue/),
    )
    const alert = screen.getByRole('alert')
    expect(within(alert).getByText(/hors de la surface analytique/)).toBeInTheDocument()
    // La requête refusée reste visible : c'est la matière de l'enquête.
    expect(screen.getByText(/FROM app.shipments/)).toBeInTheDocument()
    // Et aucun tableau de résultat n'est affiché.
    expect(screen.queryByText('Résultat')).not.toBeInTheDocument()
  })
})
