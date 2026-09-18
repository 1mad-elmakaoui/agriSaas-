/**
 * The application frame.
 *
 * Two things are deliberate. The demonstration banner is not dismissible: it is
 * shown when the organisation is flagged as a demonstration, and the whole point
 * is that nobody in a sales meeting can mistake fabricated numbers for measured
 * ones. And the navigation names only the pages that exist — an entry leading to
 * an empty screen is worse than an absent entry, because it promises.
 */
import type { ReactNode } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { storeToken } from '@/lib/api'
import { useLocale } from '@/i18n'
import type { MessageKey } from '@/i18n'

const NAVIGATION: { to: string; key: MessageKey; end?: boolean }[] = [
  { to: '/', key: 'nav.overview', end: true },
  { to: '/parcelles', key: 'nav.fields' },
  { to: '/carte', key: 'nav.map' },
  { to: '/expeditions', key: 'nav.shipments' },
  { to: '/recommandations', key: 'nav.recommendations' },
  { to: '/analyse', key: 'nav.analysis' },
  { to: '/copilote', key: 'nav.copilot' },
  { to: '/abonnement', key: 'nav.subscription' },
  { to: '/administration', key: 'nav.admin' },
]

/**
 * L'entrée « Démarrage » n'apparaît que tant que le parcours n'est pas achevé.
 *
 * C'est le serveur qui décide : l'état vient de `/demarrage`. Une entrée
 * permanente menant à quatre coches vertes occuperait la navigation pour ne rien
 * dire, et une entrée codée en dur ne disparaîtrait jamais.
 */
const ONBOARDING: { to: string; key: MessageKey; end?: boolean } = {
  to: '/demarrage',
  key: 'nav.onboarding',
}

export function Shell({
  children,
  tenantName,
  isDemo,
  showOnboarding = false,
}: {
  children: ReactNode
  tenantName: string | null
  isDemo: boolean
  showOnboarding?: boolean
}) {
  const navigate = useNavigate()
  const { t } = useLocale()

  return (
    <div className="min-h-screen">
      {isDemo && (
        <div className="bg-ink-800 px-4 py-1.5 text-center text-xs font-medium text-ink-100">
          {t('app.demoBanner')}
        </div>
      )}

      <header className="border-b border-ink-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center gap-6 px-6 py-3">
          <p className="text-sm font-semibold tracking-tight text-ink-900">
            {t('app.name')}
            {tenantName && (
              <span className="ml-2 font-normal text-ink-500">· {tenantName}</span>
            )}
          </p>
          <nav className="flex flex-1 items-center gap-1">
            {(showOnboarding ? [ONBOARDING, ...NAVIGATION] : NAVIGATION).map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `focusable rounded px-2.5 py-1.5 text-sm transition ${
                    isActive
                      ? 'bg-ink-100 font-medium text-ink-900'
                      : 'text-ink-600 hover:bg-ink-50'
                  }`
                }
              >
                {t(item.key)}
              </NavLink>
            ))}
          </nav>
          <button
            type="button"
            onClick={() => {
              storeToken(null)
              navigate('/connexion')
            }}
            className="focusable rounded px-2 py-1 text-sm text-ink-500 hover:text-ink-800"
          >
            {t('app.signOut')}
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-6">{children}</main>
    </div>
  )
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string
  subtitle?: string
  actions?: ReactNode
}) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink-900">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-ink-500">{subtitle}</p>}
      </div>
      {actions}
    </div>
  )
}
