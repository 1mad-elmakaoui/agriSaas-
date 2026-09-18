/**
 * Message catalogues.
 *
 * French is the source language and it is complete. `ar-MA` is declared and
 * **empty**, deliberately: an agronomic interface must not be machine-translated
 * and called localised. A mistranslated stress level or dose unit is a safety
 * issue, not a polish issue, so a missing Arabic key falls back to the French
 * string — visibly untranslated — rather than to a generated one.
 *
 * Note what is *not* here: the agronomic and logistics vocabulary. « Stress
 * modéré », « Irrigation recommandée », « Simulé », « Jeu de démonstration » all
 * arrive from the API as `*_label_fr` fields. That is on purpose — the same word
 * must appear in the interface, in the copilot's tool results and in the audit
 * log, and a second catalogue here would be a second place for it to drift.
 */
export type Locale = 'fr' | 'ar-MA'

export type MessageKey =
  | 'app.name'
  | 'app.demoBanner'
  | 'app.signOut'
  | 'nav.overview'
  | 'nav.fields'
  | 'nav.map'
  | 'nav.shipments'
  | 'nav.recommendations'
  | 'nav.analysis'
  | 'nav.copilot'
  | 'nav.onboarding'
  | 'nav.subscription'
  | 'nav.admin'
  | 'error.unreachable'
  | 'error.unreachableRemedy'

const fr: Record<MessageKey, string> = {
  'app.name': 'AtlasAgri',
  'app.demoBanner':
    'Jeu de démonstration — aucune valeur affichée n’est une mesure réelle.',
  'app.signOut': 'Se déconnecter',
  'nav.overview': 'Vue générale',
  'nav.fields': 'Parcelles',
  'nav.map': 'Carte',
  'nav.shipments': 'Expéditions',
  'nav.recommendations': 'Recommandations',
  'nav.analysis': 'Analyse',
  'nav.copilot': 'Copilote',
  'nav.onboarding': 'Démarrage',
  'nav.subscription': 'Abonnement',
  'nav.admin': 'Administration',
  'error.unreachable': 'Le serveur est momentanément injoignable.',
  'error.unreachableRemedy': 'Réessayez dans quelques instants.',
}

/** Empty until a native reviewer supplies it. See the module docstring. */
const arMA: Partial<Record<MessageKey, string>> = {}

export const CATALOGUES: Record<Locale, Partial<Record<MessageKey, string>>> = {
  fr,
  'ar-MA': arMA,
}

export const SOURCE_CATALOGUE = fr

/** Right-to-left locales. The map, the charts and the two panels break first. */
export function direction(locale: Locale): 'ltr' | 'rtl' {
  return locale === 'ar-MA' ? 'rtl' : 'ltr'
}

export function translate(locale: Locale, key: MessageKey): string {
  return CATALOGUES[locale][key] ?? SOURCE_CATALOGUE[key]
}
