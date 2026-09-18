/**
 * Connexion, et inscription autonome.
 *
 * Le message d'échec de la **connexion** ne distingue pas une adresse inconnue
 * d'un mot de passe faux, parce que l'API ne les distingue pas non plus : les
 * séparer laisserait la page énumérer les comptes existants.
 *
 * L'**inscription**, elle, dit qu'un courriel est déjà pris. C'est la même
 * énumération, et elle est assumée : un formulaire d'inscription qui échoue en
 * silence est inutilisable, et la personne concernée doit savoir qu'elle a déjà
 * un accès.
 *
 * Les deux formulaires vivent sur le même écran, dans deux onglets. Une page
 * d'inscription séparée obligerait à la trouver — et la §6 compte en minutes.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { RequestFailed, api, storeToken } from '@/lib/api'
import { Button, ErrorNotice } from '@/components/primitives'

export function SignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const navigate = useNavigate()
  const [mode, setMode] = useState<'login' | 'signup'>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [organisationName, setOrganisationName] = useState('')
  const [fullName, setFullName] = useState('')
  const [error, setError] = useState<{ message: string; remedy: string | null } | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const session =
        mode === 'login'
          ? await api.login(email, password)
          : await api.signup({
              organisation_name: organisationName,
              full_name: fullName,
              email,
              password,
            })
      storeToken(session.access_token)
      onSignedIn()
      // Une inscription mène au parcours de démarrage : l'organisation est
      // vide, et la vue générale d'une organisation vide n'apprend rien.
      navigate(mode === 'login' ? '/' : '/demarrage')
    } catch (cause) {
      if (cause instanceof RequestFailed) {
        setError({ message: cause.message, remedy: cause.remedyFr })
      } else {
        setError({
          message: 'Le serveur est momentanément injoignable.',
          remedy: 'Réessayez dans quelques instants.',
        })
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <h1 className="text-lg font-semibold tracking-tight text-ink-900">AtlasAgri</h1>
        <p className="mt-1 text-sm text-ink-500">
          Plateforme de décision agricole et logistique.
        </p>

        <div className="mt-5 flex gap-1 rounded-md bg-ink-100 p-1" role="tablist">
          {(
            [
              ['login', 'Se connecter'],
              ['signup', 'Créer une organisation'],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="tab"
              aria-selected={mode === value}
              onClick={() => {
                setMode(value)
                setError(null)
              }}
              className={`focusable flex-1 rounded px-2.5 py-1.5 text-sm transition ${
                mode === value
                  ? 'bg-white font-medium text-ink-900 shadow-sm'
                  : 'text-ink-600'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <form onSubmit={submit} className="panel mt-3 space-y-4 p-5">
          {mode === 'signup' && (
            <>
              <div>
                <label htmlFor="organisation" className="panel-title">
                  Nom de l'organisation
                </label>
                <input
                  id="organisation"
                  required
                  value={organisationName}
                  onChange={(e) => setOrganisationName(e.target.value)}
                  className="focusable mt-1 w-full rounded-md border border-ink-300 px-3 py-2 text-sm"
                />
              </div>
              <div>
                <label htmlFor="fullName" className="panel-title">
                  Votre nom
                </label>
                <input
                  id="fullName"
                  required
                  autoComplete="name"
                  value={fullName}
                  onChange={(e) => setFullName(e.target.value)}
                  className="focusable mt-1 w-full rounded-md border border-ink-300 px-3 py-2 text-sm"
                />
              </div>
            </>
          )}
          <div>
            <label htmlFor="email" className="panel-title">
              Adresse électronique
            </label>
            <input
              id="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="focusable mt-1 w-full rounded-md border border-ink-300 px-3 py-2 text-sm"
            />
          </div>
          <div>
            <label htmlFor="password" className="panel-title">
              Mot de passe
            </label>
            <input
              id="password"
              type="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              required
              minLength={mode === 'login' ? undefined : 12}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="focusable mt-1 w-full rounded-md border border-ink-300 px-3 py-2 text-sm"
            />
            {mode === 'signup' && (
              /* La contrainte est une longueur, et rien d'autre. Une règle de
                 composition produit des mots de passe courts et prévisibles. */
              <p className="mt-1 text-xs text-ink-500">Au moins douze caractères.</p>
            )}
          </div>
          {error && <ErrorNotice message={error.message} remedy={error.remedy} />}
          <Button type="submit" variant="primary" disabled={busy}>
            {busy
              ? 'Un instant…'
              : mode === 'login'
                ? 'Se connecter'
                : "Créer l'organisation"}
          </Button>
          {mode === 'signup' && (
            <p className="text-xs text-ink-500">
              Votre organisation démarre vide et sur le plan Coopérative. Aucun moyen
              de paiement n'est demandé : les plans sont provisionnés par un
              administrateur.
            </p>
          )}
        </form>
      </div>
    </div>
  )
}
