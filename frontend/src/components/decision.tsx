/**
 * The two panels the product rests on.
 *
 * Neither computes anything. Every number they show is read from the decision
 * payload the engine produced, because a figure recomputed for display is a
 * second version of the same figure — and the two will differ the day someone
 * changes a rounding rule on one side only.
 */
import type { Decision, ToolCall } from '@/lib/types'
import { Disclosure, NotDelivered, Panel, SourceChip, StateChip } from './primitives'

/**
 * « Pourquoi cette décision ? »
 *
 * Inputs with their provenance, then the numbered calculation steps, then the
 * assumptions, then the data-quality checklist. The order is the order an
 * agronomist argues in: what did you measure, what did you do with it, what did
 * you assume, and how much should I trust the whole thing.
 */
export function WhyThisDecision({
  decision,
  toolCalls,
}: {
  decision: Decision
  toolCalls?: ToolCall[]
}) {
  const missing = decision.inputs.filter((i) => i.value === null)
  const present = decision.inputs.filter((i) => i.value !== null)

  return (
    <Panel
      title="Pourquoi cette décision ?"
      subtitle="Les entrées, le calcul, et ce qui a été supposé"
    >
      <div className="space-y-5">
        <section>
          <p className="panel-title mb-2">Entrées du calcul</p>
          <ul aria-label="Entrées du calcul" className="divide-y divide-ink-100">
            {present.map((input) => (
              <li
                key={input.key}
                className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 py-2"
              >
                <span className="text-sm text-ink-700">{input.label_fr}</span>
                <span className="flex items-center gap-2">
                  <span className="tabular text-sm font-medium text-ink-900">
                    {typeof input.value === 'number'
                      ? new Intl.NumberFormat('fr-MA', {
                          maximumFractionDigits: 2,
                        }).format(input.value)
                      : input.value}
                    {input.unit ? ` ${input.unit}` : ''}
                  </span>
                  <SourceChip
                    state={input.state}
                    stateLabelFr={input.state_label_fr}
                    origin={input.origin}
                    originLabelFr={input.origin_label_fr}
                    sourceLabelFr={input.source_label_fr}
                  />
                </span>
              </li>
            ))}
          </ul>
          {missing.length > 0 && (
            <ul aria-label="Entrées manquantes" className="mt-2 space-y-1">
              {missing.map((input) => (
                <li key={input.key} className="text-sm text-ink-500">
                  <span className="font-medium text-ink-700">{input.label_fr}</span>
                  {' — '}
                  {input.missing_reason_fr ?? 'valeur absente.'}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section>
          <p className="panel-title mb-2">Étapes du calcul</p>
          <ol className="space-y-1.5">
            {decision.calculation_steps_fr.map((step, index) => (
              <li key={index} className="text-sm leading-relaxed text-ink-700">
                {step}
              </li>
            ))}
          </ol>
        </section>

        {decision.assumptions_fr.length > 0 && (
          <section>
            <p className="panel-title mb-2">Hypothèses</p>
            <ul className="space-y-1">
              {decision.assumptions_fr.map((assumption, index) => (
                <li key={index} className="flex gap-2 text-sm text-ink-600">
                  <span
                    className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-ink-400"
                    aria-hidden="true"
                  />
                  {assumption}
                </li>
              ))}
            </ul>
          </section>
        )}

        {decision.warnings_fr.length > 0 && (
          <section>
            <p className="panel-title mb-2">Avertissements</p>
            <ul className="space-y-1">
              {decision.warnings_fr.map((warning, index) => (
                <li key={index} className="flex gap-2 text-sm text-high">
                  <span
                    className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-high"
                    aria-hidden="true"
                  />
                  {warning}
                </li>
              ))}
            </ul>
          </section>
        )}

        <DataQuality decision={decision} />

        {toolCalls && toolCalls.length > 0 && <Calculations toolCalls={toolCalls} />}
      </div>
    </Panel>
  )
}

/**
 * The reliability checklist.
 *
 * A per-input list, not a single score. « Fiabilité : Moyenne » tells the user
 * nothing actionable; « l'humidité du sol vient du jeu de démonstration » tells
 * them exactly what to improve, and the improvement is almost always the
 * moisture reading.
 */
function DataQuality({ decision }: { decision: Decision }) {
  const weakest = decision.inputs
    .filter((i) => i.value !== null)
    .filter((i) => i.origin === 'SEED_DEMO' || i.origin === 'MODEL' || i.state === 'SIMULATED')

  return (
    <section className="rounded-md bg-ink-50 px-3.5 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="panel-title">Qualité des données</p>
        {decision.reliability_label_fr && (
          <p className="text-sm font-medium text-ink-700">
            Fiabilité : {decision.reliability_label_fr}
          </p>
        )}
      </div>
      {weakest.length > 0 ? (
        <ul className="mt-2 space-y-1">
          {weakest.map((input) => (
            <li key={input.key} className="text-sm text-ink-600">
              <span className="font-medium">{input.label_fr}</span> — {input.origin_label_fr}.
              Une mesure réelle relèverait la fiabilité de cette recommandation.
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-ink-600">
          Toutes les entrées proviennent de mesures ou de références documentées.
        </p>
      )}
      {Object.keys(decision.unavailable_fr).length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-ink-200 pt-2">
          {Object.entries(decision.unavailable_fr).map(([key, reason]) => (
            <li key={key} className="text-sm text-ink-600">
              <span className="font-medium capitalize">{key}</span> — {reason}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/**
 * « Voir les calculs » — the tool trace.
 *
 * The interface rebuilds this from the trace, never from a block the model
 * wrote. A figure the model retyped into its prose without appearing here has
 * no source, and this panel is what makes that visible.
 */
function Calculations({ toolCalls }: { toolCalls: ToolCall[] }) {
  return (
    <Disclosure summary="Voir les calculs" count={toolCalls.length}>
      <ul className="space-y-3">
        {toolCalls.map((call, index) => (
          <li key={index}>
            <p className="text-sm font-medium text-ink-800">
              {call.name}
              {!call.succeeded && (
                <span className="ml-2 text-xs font-normal text-critical">échec</span>
              )}
            </p>
            {call.error_fr ? (
              <p className="mt-0.5 text-sm text-ink-600">{call.error_fr}</p>
            ) : (
              <dl className="mt-1 grid grid-cols-[auto,1fr] gap-x-3 gap-y-0.5">
                {Object.entries(call.result)
                  .filter(([, value]) => typeof value !== 'object')
                  .slice(0, 8)
                  .map(([key, value]) => (
                    <div key={key} className="contents">
                      <dt className="text-xs text-ink-500">{key}</dt>
                      <dd className="tabular text-xs text-ink-700">{String(value)}</dd>
                    </div>
                  ))}
              </dl>
            )}
          </li>
        ))}
      </ul>
    </Disclosure>
  )
}

/**
 * « Sources et preuves »
 *
 * Business language on the surface, technical detail behind a disclosure, never
 * a raw log. A farm manager should not have to read a trace to decide whether
 * to trust a recommendation.
 */
export function SourcesAndEvidence({ decision }: { decision: Decision }) {
  return (
    <Panel title="Sources et preuves" subtitle="Ce sur quoi repose cette analyse">
      {decision.evidence.length === 0 ? (
        <NotDelivered>
          Aucune preuve n’a été enregistrée pour cette décision.
        </NotDelivered>
      ) : (
        <dl className="space-y-3">
          {decision.evidence.map((item, index) => (
            <div key={index} className="border-l-2 border-ink-200 pl-3">
              <dt className="flex flex-wrap items-center gap-2 text-sm font-medium text-ink-800">
                {item.label_fr}
                <StateChip
                  state={item.state}
                  stateLabelFr={item.state_label_fr}
                  sourceLabelFr={item.source_label_fr}
                />
              </dt>
              <dd className="mt-0.5 text-sm text-ink-600">{item.detail_fr}</dd>
            </div>
          ))}
        </dl>
      )}
    </Panel>
  )
}
