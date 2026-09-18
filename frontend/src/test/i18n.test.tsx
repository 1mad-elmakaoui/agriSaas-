/**
 * The localisation scaffold, and the refusal it encodes.
 *
 * The point of these tests is not that translation works — there is no Arabic
 * catalogue. It is that the **absence** of one behaves correctly: a missing key
 * falls back to the French source string, visibly untranslated, rather than to
 * anything generated. A machine-translated stress level is a safety issue.
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { LocaleProvider } from '@/i18n'
import { CATALOGUES, SOURCE_CATALOGUE, direction, translate } from '@/i18n/messages'
import { Shell } from '@/components/Shell'

describe('locale scaffolding', () => {
  it('has a complete French catalogue', () => {
    for (const [key, value] of Object.entries(SOURCE_CATALOGUE)) {
      expect(value, key).toBeTruthy()
    }
  })

  it('leaves the Arabic catalogue empty rather than machine-translating it', () => {
    expect(Object.keys(CATALOGUES['ar-MA'])).toEqual([])
  })

  it('falls back to the French string for a missing translation', () => {
    expect(translate('ar-MA', 'nav.fields')).toBe(SOURCE_CATALOGUE['nav.fields'])
  })

  it('knows which way each locale runs', () => {
    expect(direction('fr')).toBe('ltr')
    expect(direction('ar-MA')).toBe('rtl')
  })

  it('stamps lang and dir on the document', () => {
    render(
      <MemoryRouter>
        <LocaleProvider initial="ar-MA">
          <Shell tenantName={null} isDemo={false}>
            <p>contenu</p>
          </Shell>
        </LocaleProvider>
      </MemoryRouter>,
    )
    expect(document.documentElement.lang).toBe('ar-MA')
    expect(document.documentElement.dir).toBe('rtl')
  })
})

describe('the demonstration banner', () => {
  it('is shown when the organisation is a demonstration', () => {
    render(
      <MemoryRouter>
        <LocaleProvider>
          <Shell tenantName="Souss Primeurs" isDemo>
            <p>contenu</p>
          </Shell>
        </LocaleProvider>
      </MemoryRouter>,
    )
    expect(screen.getByText(/aucune valeur affichée n’est une mesure réelle/)).toBeInTheDocument()
  })

  it('offers no way to dismiss it', () => {
    // Dismissible, it would be dismissed in the first minute of a sales meeting,
    // which is exactly the moment it exists for.
    render(
      <MemoryRouter>
        <LocaleProvider>
          <Shell tenantName="Souss Primeurs" isDemo>
            <p>contenu</p>
          </Shell>
        </LocaleProvider>
      </MemoryRouter>,
    )
    const banner = screen.getByText(/aucune valeur affichée n’est une mesure réelle/)
    expect(banner.querySelector('button')).toBeNull()
  })

  it('is absent for a real organisation', () => {
    render(
      <MemoryRouter>
        <LocaleProvider>
          <Shell tenantName="Coopérative du Gharb" isDemo={false}>
            <p>contenu</p>
          </Shell>
        </LocaleProvider>
      </MemoryRouter>,
    )
    expect(screen.queryByText(/mesure réelle/)).not.toBeInTheDocument()
  })
})
