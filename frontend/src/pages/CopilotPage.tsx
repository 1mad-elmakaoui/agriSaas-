/**
 * Copilote.
 *
 * The rule this page exists to enforce: **the model's prose is never the primary
 * output.** What the user reads first is built from the tool trace — the
 * recommendation, the figures, the provenance of each input, the calculation
 * steps. The model's sentence sits underneath, labelled as commentary.
 *
 * This is not a stylistic preference. A number retyped by the model into a
 * paragraph has no source; a number rendered from `tool_calls[i].result` came
 * from the deterministic engine and can be traced back to it. Making the trace
 * the primary surface is what keeps the first case from ever being what the user
 * acts on.
 */
import { useState } from 'react'
import { RequestFailed, api } from '@/lib/api'
import type { CopilotAnswer, Decision, DecisionInput, ToolCall } from '@/lib/types'
import { depth, mad, rate, volume } from '@/lib/format'
import { PageHeader } from '@/components/Shell'
import { WhyThisDecision } from '@/components/decision'
import {
  Button,
  ErrorNotice,
  Figure,
  Loading,
  NotDelivered,
  Panel,
  SeverityBadge,
} from '@/components/primitives'

const SUGGESTIONS = [
  'Est-ce que je dois irriguer P03 ?',
  'Quelles parcelles ont besoin d’eau aujourd’hui ?',
  'Mon transport de tomates vers Casablanca est-il à risque ?',
]

export function CopilotPage() {
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<CopilotAnswer | null>(null)
  const [error, setError] = useState<{ message: string; remedy: string | null } | null>(null)
  const [busy, setBusy] = useState(false)

  async function ask(text: string) {
    if (!text.trim()) return
    setBusy(true)
    setError(null)
    setAnswer(null)
    try {
      setAnswer(await api.ask(text))
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
    <>
      <PageHeader
        title="Copilote"
        subtitle="Les chiffres viennent des moteurs de calcul, jamais du modèle."
      />

      <Panel>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            void ask(question)
          }}
          className="flex flex-wrap gap-2"
        >
          <label htmlFor="question" className="sr-only">
            Votre question
          </label>
          <input
            id="question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Posez une question sur vos parcelles ou vos expéditions…"
            className="focusable min-w-0 flex-1 rounded-md border border-ink-300 px-3 py-2 text-sm"
          />
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Analyse…' : 'Demander'}
          </Button>
        </form>
        <ul className="mt-3 flex flex-wrap gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <li key={suggestion}>
              <button
                type="button"
                onClick={() => {
                  setQuestion(suggestion)
                  void ask(suggestion)
                }}
                className="focusable rounded-full border border-ink-200 px-3 py-1 text-xs text-ink-600 hover:bg-ink-50"
              >
                {suggestion}
              </button>
            </li>
          ))}
        </ul>
      </Panel>

      {busy && (
        <div className="mt-5">
          <Loading label="Le copilote consulte les moteurs de calcul…" />
        </div>
      )}
      {error && (
        <div className="mt-5">
          <ErrorNotice message={error.message} remedy={error.remedy} />
        </div>
      )}
      {answer && (
        <div className="mt-5">
          <AnswerView answer={answer} />
        </div>
      )}
    </>
  )
}

function AnswerView({ answer }: { answer: CopilotAnswer }) {
  return (
    <div className="space-y-5">
      {answer.truncated && (
        <ErrorNotice
          message="L’analyse n’a pas pu être menée à son terme dans le nombre d’étapes autorisé."
          remedy="Les résultats intermédiaires ci-dessous sont complets ; la conclusion ne l’est pas."
        />
      )}

      {answer.tool_calls.length === 0 ? (
        <NotDelivered>
          Le copilote n’a consulté aucun moteur de calcul pour cette question. Sa
          réponse ne repose donc sur aucun chiffre de votre organisation.
        </NotDelivered>
      ) : (
        answer.tool_calls.map((call, index) => <ToolResult key={index} call={call} />)
      )}

      <Panel
        title="Commentaire du copilote"
        subtitle="Texte rédigé par le modèle — les chiffres font foi ci-dessus"
      >
        <p className="whitespace-pre-line text-sm leading-relaxed text-ink-600">
          {answer.text_fr}
        </p>
        <p className="mt-3 border-t border-ink-200 pt-2 text-xs text-ink-500">
          {answer.model} · invite {answer.prompt_version} · {answer.iterations} étape(s) ·{' '}
          {answer.cost_label_fr}
        </p>
      </Panel>
    </div>
  )
}

/** Narrowing helpers: the trace is `Record<string, unknown>` by construction. */
function asNumber(value: unknown): number | null {
  return typeof value === 'number' ? value : null
}
function asString(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}
function asRecord(value: unknown): Record<string, string> {
  if (value === null || typeof value !== 'object') return {}
  const out: Record<string, string> = {}
  for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
    if (typeof item === 'string') out[key] = item
  }
  return out
}
function asStrings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []
}
function asInputs(value: unknown): DecisionInput[] {
  if (!Array.isArray(value)) return []
  return value.filter((entry): entry is DecisionInput => {
    if (entry === null || typeof entry !== 'object') return false
    const candidate = entry as Partial<DecisionInput>
    return typeof candidate.key === 'string' && typeof candidate.label_fr === 'string'
  })
}

function ToolResult({ call }: { call: ToolCall }) {
  if (!call.succeeded) {
    return (
      <ErrorNotice
        message={call.error_fr ?? 'Un calcul n’a pas abouti.'}
        remedy="Aucune valeur n’a été substituée au résultat manquant."
      />
    )
  }
  if (call.name === 'calculate_irrigation_requirement') {
    return <IrrigationResult result={call.result} />
  }
  return <GenericResult call={call} />
}

/**
 * The irrigation answer, rendered as a decision rather than as a sentence.
 *
 * Every figure here is read from the tool result. None of them appears because
 * the model mentioned it.
 */
function IrrigationResult({ result }: { result: Record<string, unknown> }) {
  const unavailable = asRecord(result.unavailable_fr)
  const volumeM3 = asNumber(result.water_volume_m3)
  const netMm = asNumber(result.net_requirement_mm)
  const durationMinutes = asNumber(result.duration_minutes)
  const cost = asNumber(result.estimated_cost_mad)
  const et0 = asNumber(result.et0_mm_day)
  const etc = asNumber(result.etc_mm_day)
  const recommendation = asString(result.recommendation)

  // Adapted to the shared decision shape, so the copilot and the parcelle page
  // render the same panel from the same component.
  const decision: Decision = {
    domain: 'IRRIGATION',
    domain_label_fr: 'Irrigation',
    subject_id: asString(result.field_code) ?? '',
    subject_label_fr: asString(result.field_code) ?? '',
    headline_fr: asString(result.headline_fr) ?? '',
    outcome_code: recommendation ?? '',
    inputs: asInputs(result.inputs),
    calculation_steps_fr: asStrings(result.calculation_steps_fr),
    assumptions_fr: asStrings(result.assumptions_fr),
    evidence: [],
    tradeoffs_fr: [],
    warnings_fr: asStrings(result.warnings_fr),
    reliability: null,
    reliability_label_fr: asString(result.reliability_label_fr),
    unavailable_fr: unavailable,
  }

  return (
    <div className="space-y-5">
      <Panel
        title={`Parcelle ${asString(result.field_code) ?? ''}`}
        actions={
          <SeverityBadge
            severity={recommendation === 'IRRIGATE' ? 'high' : 'low'}
            label={asString(result.recommendation_label_fr) ?? recommendation ?? '—'}
          />
        }
      >
        <p className="text-sm text-ink-700">{decision.headline_fr}</p>
        <div className="mt-4 grid grid-cols-2 gap-4 border-t border-ink-200 pt-4 md:grid-cols-4">
          <Figure label="Volume" value={volumeM3 === null ? null : volume(volumeM3)} emphasis />
          <Figure label="Dose nette" value={netMm === null ? null : depth(netMm)} />
          <Figure
            label="Durée"
            value={durationMinutes === null ? null : `${Math.round(durationMinutes)} min`}
            missingReason={unavailable.duree}
          />
          <Figure
            label="Coût estimé"
            value={cost === null ? null : mad(cost)}
            missingReason={unavailable.cout}
          />
        </div>
        <div className="mt-4 grid grid-cols-2 gap-4 border-t border-ink-200 pt-4 md:grid-cols-4">
          <Figure label="ET0" value={et0 === null ? null : rate(et0)} />
          <Figure label="ETc" value={etc === null ? null : rate(etc)} />
        </div>
      </Panel>
      <WhyThisDecision decision={decision} />
    </div>
  )
}

/** Any other tool: its fields, plainly, with nothing invented around them. */
function GenericResult({ call }: { call: ToolCall }) {
  const entries = Object.entries(call.result).filter(
    ([, value]) => typeof value !== 'object' || value === null,
  )
  return (
    <Panel title={call.name} subtitle="Résultat du moteur, tel quel">
      <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
        {entries.map(([key, value]) => (
          <div key={key} className="flex items-baseline justify-between gap-3 border-b border-ink-100 pb-1">
            <dt className="text-sm text-ink-600">{key}</dt>
            <dd className="tabular text-sm font-medium text-ink-900">
              {value === null ? '—' : String(value)}
            </dd>
          </div>
        ))}
      </dl>
    </Panel>
  )
}
