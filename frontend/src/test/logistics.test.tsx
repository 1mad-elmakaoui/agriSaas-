/**
 * La décision logistique à l'écran.
 *
 * Ce fichier pin les propriétés qui séparent une recommandation d'un oracle :
 * ce qui a été écarté est visible avec son motif chiffré, l'indicateur ne
 * s'affiche jamais sans sa réserve, et une option infaisable ne porte aucun
 * chiffre qui la ferait paraître comparable.
 */
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import {
  AlternativesTable,
  ExposureTimeline,
  RecommendationPanel,
} from '@/components/logistics'
import { shipmentRisk } from './fixtures'

describe('le bandeau de recommandation', () => {
  it('énonce la conclusion avant tout le reste', () => {
    render(<RecommendationPanel risk={shipmentRisk} />)
    expect(screen.getByText(/via Safi, El Jadida pour 669 MAD de plus/)).toBeInTheDocument()
  })

  it('chiffre ce que la recommandation évite et ce qu’elle coûte', () => {
    render(<RecommendationPanel risk={shipmentRisk} />)
    expect(screen.getByText('Exposition évitée')).toBeInTheDocument()
    expect(screen.getByText('+669 MAD')).toBeInTheDocument()
    expect(screen.getByText('+1,5 h')).toBeInTheDocument()
  })

  it('n’affiche jamais l’indicateur sans sa réserve', () => {
    // Affiché seul, il devient une probabilité dans la tête du lecteur, et
    // c'est irréversible.
    render(<RecommendationPanel risk={shipmentRisk} />)
    const indicator = screen.getByText(/Indicateur de perturbation/)
    const paragraph = indicator.closest('p')
    expect(paragraph).not.toBeNull()
    expect((paragraph as HTMLElement).textContent).toContain('pas une probabilité')
  })

  it('ne prétend pas à une fiabilité élevée sur un jeu de démonstration', () => {
    render(<RecommendationPanel risk={shipmentRisk} />)
    expect(screen.getByText('Faible')).toBeInTheDocument()
  })
})

describe('le tableau des options', () => {
  it('sépare les options applicables de celles qui ont été écartées', () => {
    render(<AlternativesTable risk={shipmentRisk} />)
    expect(screen.getByText(/2 applicable\(s\), 1 écartée\(s\)/)).toBeInTheDocument()
  })

  it('marque la recommandation et le plan actuel distinctement', () => {
    render(<AlternativesTable risk={shipmentRisk} />)
    expect(screen.getByText('Recommandé')).toBeInTheDocument()
    expect(screen.getByText('Actuel')).toBeInTheDocument()
  })

  it('ne met aucun chiffre sur une option écartée', async () => {
    // Les renseigner la ferait figurer dans le tableau comme un choix possible.
    render(<AlternativesTable risk={shipmentRisk} />)
    await userEvent.click(
      screen.getByRole('button', { name: /Options examinées puis écartées/ }),
    )
    const rejected = screen.getByText('Avancer le départ de 10 h').closest('li')
    expect(rejected).not.toBeNull()
    expect((rejected as HTMLElement).textContent).not.toMatch(/MAD/)
    expect((rejected as HTMLElement).textContent).toContain('minimum de 3 h')
  })

  it('garde les options écartées visibles, repliées mais dénombrées', () => {
    render(<AlternativesTable risk={shipmentRisk} />)
    const disclosure = screen.getByRole('button', {
      name: /Options examinées puis écartées/,
    })
    expect(within(disclosure).getByText('1')).toBeInTheDocument()
    // Fermé par défaut : présent sans encombrer.
    expect(screen.queryByText(/minimum de 3 h/)).not.toBeInTheDocument()
  })

  it('laisse la recommandation piloter la carte, jamais l’inverse', async () => {
    const onSelect = vi.fn()
    render(<AlternativesTable risk={shipmentRisk} onSelect={onSelect} />)
    await userEvent.click(screen.getByRole('button', { name: /Alternative 1/ }))
    expect(onSelect).toHaveBeenCalledOnce()
    expect(onSelect.mock.calls[0]?.[0]).toMatchObject({ id: 'itineraire-1' })
  })
})

describe('la chronologie d’exposition', () => {
  it('situe le tronçon exposé dans le temps, pas seulement sur la carte', () => {
    render(<ExposureTimeline risk={shipmentRisk} />)
    expect(screen.getByText(/Imi n’Tanoute/)).toBeInTheDocument()
    expect(screen.getByText(/à 1.8 h du départ/)).toBeInTheDocument()
    expect(screen.getByText(/seuil de vigilance 30 mm/)).toBeInTheDocument()
  })

  it('dit explicitement quand rien n’est exposé', () => {
    render(<ExposureTimeline risk={{ ...shipmentRisk, exposed_segments: [] }} />)
    expect(
      screen.getByText(/ne se trouve dans aucune zone perturbée/),
    ).toBeInTheDocument()
  })
})

describe('la carte', () => {
  it('ne fait pas tomber la décision quand elle ne peut pas s’afficher', async () => {
    // jsdom n'a pas de WebGL — comme un poste de bureau verrouillé ou un pilote
    // ancien. Sans garde, l'exception de MapLibre emporte toute la page, et
    // l'exploitant perd la recommandation en même temps que le fond de carte.
    const { RouteMap } = await import('@/components/RouteMap')
    render(<RouteMap risk={shipmentRisk} selected={null} />)
    expect(screen.getByText(/n’a pas pu s’afficher/)).toBeInTheDocument()
    // La légende reste : ce qui est lisible sans fond de carte doit le rester.
    expect(screen.getByText('Itinéraire recommandé')).toBeInTheDocument()
  })
})
