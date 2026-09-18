/**
 * Routing and the session gate.
 *
 * The tenant name and the demonstration flag come from the overview endpoint
 * rather than from the token: they are facts about the organisation, and reading
 * them from the server means a tenant that stops being a demonstration stops
 * showing the banner without anyone re-issuing a token.
 */
import { useCallback, useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { api, storedToken } from './lib/api'
import { Shell } from './components/Shell'
import { AdminPage } from './pages/AdminPage'
import { AnalysisPage } from './pages/AnalysisPage'
import { CopilotPage } from './pages/CopilotPage'
import { FieldsPage } from './pages/FieldsPage'
import { MapPage } from './pages/MapPage'
import { OnboardingPage } from './pages/OnboardingPage'
import { OverviewPage } from './pages/OverviewPage'
import { RecommendationsPage } from './pages/RecommendationsPage'
import { ShipmentsPage } from './pages/ShipmentsPage'
import { SubscriptionPage } from './pages/SubscriptionPage'
import { SignIn } from './pages/SignIn'

export function App() {
  const [signedIn, setSignedIn] = useState(() => storedToken() !== null)
  const [tenant, setTenant] = useState<{ name: string; isDemo: boolean } | null>(null)
  // `null` tant qu'on ne sait pas : l'entrée « Démarrage » n'apparaît que sur un
  // « non » explicite du serveur, jamais sur une supposition en attendant la
  // réponse.
  const [onboardingDone, setOnboardingDone] = useState<boolean | null>(null)

  const refreshTenant = useCallback(() => {
    if (!signedIn) {
      setTenant(null)
      setOnboardingDone(null)
      return
    }
    api
      .onboarding()
      .then((state) => setOnboardingDone(state.complete))
      .catch(() => setOnboardingDone(null))
    api
      .overview()
      .then((overview) =>
        setTenant({ name: overview.tenant_name_fr, isDemo: overview.is_demo }),
      )
      .catch(() => {
        // The banner is not worth failing a page over. If the organisation
        // cannot be read, the page it belongs to will report the error itself.
        setTenant(null)
      })
  }, [signedIn])

  useEffect(refreshTenant, [refreshTenant])

  if (!signedIn) {
    return (
      <Routes>
        <Route path="*" element={<SignIn onSignedIn={() => setSignedIn(true)} />} />
      </Routes>
    )
  }

  return (
    <Shell
      tenantName={tenant?.name ?? null}
      isDemo={tenant?.isDemo ?? false}
      showOnboarding={onboardingDone === false}
    >
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/parcelles" element={<FieldsPage />} />
        <Route path="/parcelles/:code" element={<FieldsPage />} />
        <Route path="/carte" element={<MapPage />} />
        <Route path="/expeditions" element={<ShipmentsPage />} />
        <Route path="/recommandations" element={<RecommendationsPage />} />
        <Route path="/analyse" element={<AnalysisPage />} />
        <Route path="/copilote" element={<CopilotPage />} />
        <Route path="/demarrage" element={<OnboardingPage />} />
        <Route path="/abonnement" element={<SubscriptionPage />} />
        <Route path="/administration" element={<AdminPage />} />
        <Route path="/connexion" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  )
}
