/**
 * Démarrage — de l'inscription au premier avis d'irrigation.
 *
 * L'objectif est une durée : moins de dix minutes. Ce qui coûte ces minutes
 * n'est pas la saisie, c'est de deviner — quel code de culture existe, pourquoi
 * l'avis est bloqué, ce qu'il reste à faire.
 *
 * L'écran ne connaît donc **aucune** des deux choses qu'il affiche : les étapes
 * viennent du serveur, les valeurs proposées viennent du catalogue de
 * l'organisation. Le jour où une étape disparaît, la page cesse de la demander
 * sans qu'on la modifie ; le jour où une culture est ajoutée, elle apparaît dans
 * la liste sans qu'on la touche.
 *
 * Deux champs restent volontairement absents du formulaire : le débit et le
 * tarif de l'eau. Ils sont facultatifs et le resteront ici — sans débit il n'y a
 * pas de durée d'arrosage, et c'est exactement ce que la fiche de la parcelle
 * doit dire. Les demander dans un formulaire de démarrage inciterait à inventer
 * un chiffre pour passer à l'étape suivante.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { RequestFailed, api } from '@/lib/api'
import { useResource } from '@/lib/useResource'
import type { OnboardingStep, ReferenceChoice } from '@/lib/types'
import { PageHeader } from '@/components/Shell'
import { Button, ErrorNotice, Loading, Panel } from '@/components/primitives'

function StepRow({ step, index }: { step: OnboardingStep; index: number }) {
  return (
    <li className="flex gap-3 py-2.5">
      <span
        aria-hidden="true"
        className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${
          step.done ? 'bg-low text-white' : 'bg-ink-100 text-ink-500'
        }`}
      >
        {step.done ? '✓' : index + 1}
      </span>
      <div>
        <p className="text-sm font-medium text-ink-900">
          {step.title_fr}
          <span className="sr-only">{step.done ? ' — étape franchie' : ' — à faire'}</span>
        </p>
        <p className="text-xs text-ink-600">{step.detail_fr}</p>
        {/* Une étape franchie n'a pas d'action : le serveur rend `null`, et la
            page n'invente pas « Terminé ✓ » à la place. */}
        {step.action_fr && (
          <p className="mt-0.5 text-xs font-medium text-recommended">{step.action_fr}</p>
        )}
      </div>
    </li>
  )
}

function Select({
  id,
  label,
  value,
  choices,
  onChange,
}: {
  id: string
  label: string
  value: string
  choices: ReferenceChoice[]
  onChange: (value: string) => void
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-xs text-ink-600">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="focusable mt-1 w-full rounded border border-ink-300 bg-white px-2.5 py-1.5 text-sm"
      >
        <option value="">—</option>
        {choices.map((choice) => (
          <option key={choice.code} value={choice.code}>
            {choice.name_fr}
            {/* Une ligne locale masque la ligne globale de même code : deux
                valeurs de même nom n'engagent pas la même chose. */}
            {choice.is_local ? ' (valeur de votre organisation)' : ''}
          </option>
        ))}
      </select>
    </div>
  )
}

export function OnboardingPage() {
  const state = useResource(() => api.onboarding())
  const choices = useResource(() => api.onboardingChoices())
  const navigate = useNavigate()

  const [error, setError] = useState<{ message: string; remedy: string | null } | null>(null)
  const [busy, setBusy] = useState(false)
  const [createdCode, setCreatedCode] = useState<string | null>(null)
  const [moisture, setMoisture] = useState('')
  const [draft, setDraft] = useState({
    code: '',
    name_fr: '',
    site_code: '',
    area_ha: '',
    latitude: '',
    longitude: '',
    crop_code: '',
    soil_code: '',
    irrigation_system_code: '',
  })

  function update(key: keyof typeof draft, value: string) {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  async function guard(run: () => Promise<void>) {
    setError(null)
    setBusy(true)
    try {
      await run()
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

  const target = createdCode ?? state.data?.first_field_code ?? null

  return (
    <>
      <PageHeader
        title="Démarrage"
        subtitle="Une parcelle, un relevé, un premier avis d'irrigation."
      />

      {error && <ErrorNotice message={error.message} remedy={error.remedy} />}

      <div className="space-y-5">
        <Panel title="Où vous en êtes">
          {state.loading && <Loading label="Chargement…" />}
          {state.error && (
            <ErrorNotice message={state.error.message} remedy={state.error.remedy} />
          )}
          {state.data && (
            <ol className="divide-y divide-ink-100">
              {state.data.steps.map((step, index) => (
                <StepRow key={step.key} step={step} index={index} />
              ))}
            </ol>
          )}
        </Panel>

        {choices.data && choices.data.sites.length === 0 && (
          <Panel title="Déclarer une exploitation">
            <p className="text-sm text-ink-700">
              Aucun site d'exploitation n'existe encore. Une parcelle se rattache à un
              site, donc c'est par là qu'il faut commencer.
            </p>
          </Panel>
        )}

        {choices.data && choices.data.sites.length > 0 && (
          <Panel
            title="Créer une parcelle"
            subtitle="Le débit et le tarif de l'eau se renseignent plus tard : sans eux, la durée et le coût sont déclarés indisponibles plutôt qu'estimés."
          >
            <form
              className="grid gap-3 sm:grid-cols-2"
              onSubmit={(event) => {
                event.preventDefault()
                void guard(async () => {
                  const field = await api.createField({
                    code: draft.code,
                    name_fr: draft.name_fr,
                    site_code: draft.site_code,
                    area_ha: Number(draft.area_ha),
                    latitude: Number(draft.latitude),
                    longitude: Number(draft.longitude),
                    crop_code: draft.crop_code,
                    soil_code: draft.soil_code,
                    irrigation_system_code: draft.irrigation_system_code,
                  })
                  setCreatedCode(field.code)
                  state.reload()
                })
              }}
            >
              <div>
                <label htmlFor="code" className="block text-xs text-ink-600">
                  Code de la parcelle
                </label>
                <input
                  id="code"
                  required
                  value={draft.code}
                  onChange={(event) => update('code', event.target.value)}
                  className="focusable mt-1 w-full rounded border border-ink-300 px-2.5 py-1.5 text-sm"
                />
              </div>
              <div>
                <label htmlFor="name" className="block text-xs text-ink-600">
                  Nom
                </label>
                <input
                  id="name"
                  required
                  value={draft.name_fr}
                  onChange={(event) => update('name_fr', event.target.value)}
                  className="focusable mt-1 w-full rounded border border-ink-300 px-2.5 py-1.5 text-sm"
                />
              </div>
              <Select
                id="site"
                label="Site d'exploitation"
                value={draft.site_code}
                choices={choices.data.sites}
                onChange={(value) => update('site_code', value)}
              />
              <div>
                <label htmlFor="area" className="block text-xs text-ink-600">
                  Surface (ha)
                </label>
                <input
                  id="area"
                  required
                  type="number"
                  step="0.01"
                  min="0.01"
                  value={draft.area_ha}
                  onChange={(event) => update('area_ha', event.target.value)}
                  className="focusable mt-1 w-full rounded border border-ink-300 px-2.5 py-1.5 text-sm"
                />
              </div>
              <div>
                <label htmlFor="latitude" className="block text-xs text-ink-600">
                  Latitude
                </label>
                <input
                  id="latitude"
                  required
                  type="number"
                  step="0.000001"
                  value={draft.latitude}
                  onChange={(event) => update('latitude', event.target.value)}
                  className="focusable mt-1 w-full rounded border border-ink-300 px-2.5 py-1.5 text-sm"
                />
              </div>
              <div>
                <label htmlFor="longitude" className="block text-xs text-ink-600">
                  Longitude
                </label>
                <input
                  id="longitude"
                  required
                  type="number"
                  step="0.000001"
                  value={draft.longitude}
                  onChange={(event) => update('longitude', event.target.value)}
                  className="focusable mt-1 w-full rounded border border-ink-300 px-2.5 py-1.5 text-sm"
                />
              </div>
              <Select
                id="crop"
                label="Culture"
                value={draft.crop_code}
                choices={choices.data.crops}
                onChange={(value) => update('crop_code', value)}
              />
              <Select
                id="soil"
                label="Sol"
                value={draft.soil_code}
                choices={choices.data.soils}
                onChange={(value) => update('soil_code', value)}
              />
              <Select
                id="system"
                label="Système d'irrigation"
                value={draft.irrigation_system_code}
                choices={choices.data.irrigation_systems}
                onChange={(value) => update('irrigation_system_code', value)}
              />
              <div className="flex items-end">
                <Button type="submit" variant="primary" disabled={busy}>
                  Créer la parcelle
                </Button>
              </div>
            </form>
          </Panel>
        )}

        {target && (
          <Panel
            title={`Saisir un relevé d'humidité — ${target}`}
            subtitle="Une mesure au tensiomètre ou à la sonde portative suffit. Elle sera enregistrée comme saisie manuelle, jamais comme mesure de capteur."
          >
            <form
              className="flex flex-wrap items-end gap-3"
              onSubmit={(event) => {
                event.preventDefault()
                void guard(async () => {
                  await api.recordMoisture(target, Number(moisture))
                  setMoisture('')
                  state.reload()
                })
              }}
            >
              <div>
                <label htmlFor="moisture" className="block text-xs text-ink-600">
                  Humidité du sol (%)
                </label>
                <input
                  id="moisture"
                  required
                  type="number"
                  step="0.1"
                  min="0"
                  max="100"
                  value={moisture}
                  onChange={(event) => setMoisture(event.target.value)}
                  className="focusable mt-1 w-40 rounded border border-ink-300 px-2.5 py-1.5 text-sm"
                />
              </div>
              <Button type="submit" variant="primary" disabled={busy}>
                Enregistrer le relevé
              </Button>
              <Button onClick={() => navigate(`/parcelles/${encodeURIComponent(target)}`)}>
                Voir l'avis d'irrigation
              </Button>
            </form>
          </Panel>
        )}
      </div>
    </>
  )
}
