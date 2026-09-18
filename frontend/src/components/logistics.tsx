/**
 * La décision logistique, rendue en interface structurée.
 *
 * Même règle que partout : rien n'est recalculé ici. Les écarts de coût, de
 * durée et de risque arrivent déjà calculés par rapport au plan actuel, parce
 * qu'un écart recalculé à l'affichage est un second exemplaire du même nombre.
 *
 * Ce que ces composants doivent réussir tient en une phrase : montrer *ce qui a
 * été écarté et pourquoi*, aussi clairement que ce qui a été retenu. Une
 * recommandation qui n'affiche que l'option gagnante ressemble à un oracle ;
 * une qui montre ses rejets se laisse contester, ce qui est la seule façon
 * d'être crue.
 */
import type { ExposedSegment, RiskLevel, ShipmentRisk, TransportAlternative } from '@/lib/types'
import { dateTime, hoursFromNow, mad, percent } from '@/lib/format'
import {
  Disclosure,
  Figure,
  Panel,
  type Severity,
  SeverityBadge,
} from './primitives'

export const RISK_SEVERITY: Record<RiskLevel, Severity> = {
  LOW: 'low',
  MODERATE: 'moderate',
  HIGH: 'high',
  CRITICAL: 'critical',
}

function signed(value: number, unit: string, fractionDigits = 0): string {
  const formatted = new Intl.NumberFormat('fr-FR', {
    maximumFractionDigits: fractionDigits,
    signDisplay: 'exceptZero',
  }).format(value)
  return `${formatted} ${unit}`
}

/** Le bandeau de recommandation : la conclusion, avant tout le reste. */
export function RecommendationPanel({ risk }: { risk: ShipmentRisk }) {
  const recommended = risk.alternatives.find((a) => a.is_recommended)
  const current = risk.alternatives.find((a) => a.is_current_plan)
  const changing = recommended !== undefined && !recommended.is_current_plan

  return (
    <Panel
      title="Recommandation"
      subtitle={`Arbitrage selon le profil « ${risk.profile_fr} »`}
      actions={
        <SeverityBadge
          severity={RISK_SEVERITY[risk.risk_level]}
          label={`Plan actuel : ${risk.risk_level_label_fr.toLowerCase()}`}
        />
      }
    >
      <p className="text-base font-medium text-ink-900">{risk.headline_fr}</p>
      <p className="mt-1 text-sm text-ink-600">{risk.profile_rationale_fr}</p>

      {changing && recommended && current && (
        <div className="mt-4 grid grid-cols-2 gap-4 border-t border-ink-200 pt-4 md:grid-cols-4">
          <Figure
            label="Exposition évitée"
            value={
              recommended.risk_delta === null
                ? null
                : // Une magnitude, sans signe : « +0,624 point de risque » sous
                  // un titre « exposition évitée » se lit à l'envers.
                  `${new Intl.NumberFormat('fr-FR', {
                    maximumFractionDigits: 2,
                  }).format(Math.abs(recommended.risk_delta))} point de risque`
            }
            emphasis
          />
          <Figure
            label="Écart de coût"
            value={
              recommended.cost_delta_mad === null
                ? null
                : recommended.cost_delta_mad === 0
                  ? 'sans surcoût'
                  : signed(recommended.cost_delta_mad, 'MAD')
            }
          />
          <Figure
            label="Écart de durée"
            value={
              recommended.duration_delta_hours === null
                ? null
                : signed(recommended.duration_delta_hours, 'h', 1)
            }
          />
          <Figure
            label="Marge sur l’échéance"
            value={
              recommended.sla_margin_hours === null
                ? null
                : hoursFromNow(recommended.sla_margin_hours)
            }
          />
        </div>
      )}

      <div className="mt-4 grid grid-cols-2 gap-4 border-t border-ink-200 pt-4 md:grid-cols-4">
        <Figure
          label="Part du trajet exposée"
          value={`${percent(risk.exposure_fraction * 100)} · ${Math.round(risk.exposed_distance_km)} km`}
        />
        <Figure label="Départ prévu" value={dateTime(risk.departure_at)} />
        <Figure label="Échéance de service" value={dateTime(risk.sla_deadline_at)} />
        <Figure
          label="Fiabilité de l’analyse"
          value={risk.reliability_label_fr}
          missingReason="Fiabilité non calculée."
        />
      </div>

      {/* L'indicateur ne voyage jamais sans sa réserve. Affiché seul, il devient
          une probabilité dans la tête du lecteur, et c'est irréversible. */}
      <p className="mt-4 rounded-md bg-ink-50 px-3 py-2 text-xs text-ink-600">
        <span className="font-medium text-ink-800">
          Indicateur de perturbation : {risk.disruption_indicator.toFixed(2)}
        </span>{' '}
        — {risk.disruption_caveat_fr}
      </p>
    </Panel>
  )
}

/** Le tableau comparatif. La recommandation est mise en évidence, pas isolée. */
export function AlternativesTable({
  risk,
  selectedId,
  onSelect,
}: {
  risk: ShipmentRisk
  selectedId?: string | null
  onSelect?: (option: TransportAlternative) => void
}) {
  const feasible = risk.alternatives.filter((a) => a.is_feasible)
  const rejected = risk.alternatives.filter((a) => !a.is_feasible)

  return (
    <Panel
      title="Options examinées"
      subtitle={`${feasible.length} applicable(s), ${rejected.length} écartée(s)`}
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead>
            <tr className="border-b border-ink-200 text-left text-[11px] uppercase tracking-wider text-ink-500">
              <th className="py-2 pr-3 font-semibold">Option</th>
              <th className="py-2 pr-3 font-semibold">Risque</th>
              <th className="py-2 pr-3 font-semibold">Coût</th>
              <th className="py-2 pr-3 font-semibold">Durée</th>
              <th className="py-2 pr-3 font-semibold">Marge</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100">
            {feasible.map((option) => (
              <AlternativeRow
                key={option.id}
                option={option}
                selected={selectedId === option.id}
                onSelect={onSelect}
              />
            ))}
          </tbody>
        </table>
      </div>

      {rejected.length > 0 && (
        <div className="mt-4">
          <RejectedOptions options={rejected} />
        </div>
      )}
    </Panel>
  )
}

function AlternativeRow({
  option,
  selected,
  onSelect,
}: {
  option: TransportAlternative
  selected?: boolean
  onSelect?: (option: TransportAlternative) => void
}) {
  const interactive = onSelect !== undefined && option.path_lonlat.length > 0
  return (
    <tr
      className={
        selected
          ? 'bg-ink-100'
          : option.is_recommended
            ? 'bg-recommended/5'
            : interactive
              ? 'hover:bg-ink-50'
              : undefined
      }
    >
      <td className="py-2.5 pr-3">
        <div className="flex items-center gap-2">
          {option.is_recommended && (
            <span className="rounded bg-recommended px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white">
              Recommandé
            </span>
          )}
          {option.is_current_plan && !option.is_recommended && (
            <span className="rounded border border-ink-300 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-ink-500">
              Actuel
            </span>
          )}
          {interactive ? (
            <button
              type="button"
              onClick={() => onSelect?.(option)}
              className="focusable rounded text-left"
              aria-pressed={selected}
            >
              <p className="font-medium text-ink-900 underline decoration-ink-300 underline-offset-2">
                {option.label_fr}
              </p>
              <p className="text-xs text-ink-500">{option.description_fr}</p>
            </button>
          ) : (
            <div>
              <p className="font-medium text-ink-900">{option.label_fr}</p>
              <p className="text-xs text-ink-500">{option.description_fr}</p>
            </div>
          )}
        </div>
      </td>
      <td className="py-2.5 pr-3">
        {option.risk_level && option.risk_level_label_fr ? (
          <SeverityBadge
            severity={RISK_SEVERITY[option.risk_level]}
            label={option.risk_level_label_fr}
          />
        ) : (
          '—'
        )}
      </td>
      <td className="tabular py-2.5 pr-3">
        {mad(option.cost_mad)}
        <Delta value={option.cost_delta_mad} unit="MAD" lowerIsBetter />
      </td>
      <td className="tabular py-2.5 pr-3">
        {option.duration_hours === null ? '—' : `${option.duration_hours.toFixed(1)} h`}
        <Delta value={option.duration_delta_hours} unit="h" digits={1} lowerIsBetter />
      </td>
      <td className="tabular py-2.5 pr-3">
        {option.sla_margin_hours === null
          ? '—'
          : `${option.sla_margin_hours.toFixed(1)} h`}
      </td>
    </tr>
  )
}

function Delta({
  value,
  unit,
  digits = 0,
  lowerIsBetter,
}: {
  value: number | null
  unit: string
  digits?: number
  lowerIsBetter?: boolean
}) {
  if (value === null || value === 0) return null
  const favourable = lowerIsBetter ? value < 0 : value > 0
  return (
    <span className={`ml-1.5 text-xs font-medium ${favourable ? 'text-low' : 'text-high'}`}>
      {signed(value, unit, digits)}
    </span>
  )
}

/**
 * Les options écartées, avec leur motif chiffré.
 *
 * Repliées mais dénombrées : l'utilisateur voit d'un coup d'œil que des pistes
 * ont été examinées, et peut vérifier laquelle et pourquoi.
 */
function RejectedOptions({ options }: { options: TransportAlternative[] }) {
  return (
    <Disclosure summary="Options examinées puis écartées" count={options.length}>
      <ul className="space-y-2.5">
        {options.map((option) => (
          <li key={option.id}>
            <p className="text-sm font-medium text-ink-800">{option.label_fr}</p>
            <ul className="mt-0.5 space-y-0.5">
              {option.rejection_reasons_fr.map((reason, index) => (
                <li key={index} className="flex gap-2 text-sm text-ink-600">
                  <span
                    className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-ink-400"
                    aria-hidden="true"
                  />
                  {reason}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </Disclosure>
  )
}

/**
 * La chronologie d'exposition.
 *
 * Répond à la question la plus opérationnelle de toutes : combien de temps
 * reste-t-il pour agir, et à quelle heure le camion entre-t-il dans la zone.
 * Un tableau de tronçons ne répond pas à celle-là.
 */
export function ExposureTimeline({ risk }: { risk: ShipmentRisk }) {
  if (risk.exposed_segments.length === 0) {
    return (
      <Panel title="Fenêtres d’exposition">
        <p className="text-sm text-low">
          Aucun tronçon exposé sur la fenêtre de passage prévue : le véhicule ne se
          trouve dans aucune zone perturbée au moment où il l’emprunte.
        </p>
      </Panel>
    )
  }

  const departure = new Date(risk.departure_at).getTime()
  const deadline = new Date(risk.sla_deadline_at).getTime()
  const span = Math.max(deadline - departure, 1)
  const position = (iso: string) =>
    Math.max(0, Math.min(100, ((new Date(iso).getTime() - departure) / span) * 100))

  return (
    <Panel
      title="Fenêtres d’exposition"
      subtitle="Le véhicule est situé dans le temps, pas seulement sur la carte"
    >
      <div className="relative mt-6 h-2 rounded-full bg-ink-100">
        {risk.exposed_segments.map((segment, index) => {
          const left = position(segment.entry_at)
          const right = position(segment.exit_at)
          return (
            <div
              key={index}
              title={`${segment.from_name_fr} → ${segment.to_name_fr} · ${segment.risk_level_label_fr}`}
              className="absolute top-0 h-2 rounded-full"
              style={{
                left: `${left}%`,
                width: `${Math.max(1.5, right - left)}%`,
                background:
                  segment.risk_level === 'CRITICAL'
                    ? '#b3261e'
                    : segment.risk_level === 'HIGH'
                      ? '#c2570f'
                      : '#b8860b',
              }}
            />
          )
        })}
        <Marker position={0} label="Départ" />
        <Marker position={100} label="Échéance" alignRight />
      </div>

      <ul className="mt-10 space-y-3">
        {risk.exposed_segments.map((segment, index) => (
          <li key={index}>
            <div className="flex flex-wrap items-baseline gap-2">
              <p className="text-sm font-medium text-ink-900">
                {segment.from_name_fr} → {segment.to_name_fr}
              </p>
              <span className="text-xs text-ink-500">{segment.road_ref}</span>
              <SeverityBadge
                severity={RISK_SEVERITY[segment.risk_level]}
                label={segment.risk_level_label_fr}
              />
              <span className="text-xs text-ink-500">
                à {segment.hours_from_departure.toFixed(1)} h du départ ·{' '}
                {dateTime(segment.entry_at)}
              </span>
            </div>
            <ul className="mt-0.5 space-y-0.5">
              {segment.reasons_fr.map((reason, position) => (
                <li key={position} className="text-sm text-ink-600">
                  {reason}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

function Marker({
  position,
  label,
  alignRight,
}: {
  position: number
  label: string
  alignRight?: boolean
}) {
  return (
    <div className="absolute -top-1 flex flex-col items-center" style={{ left: `${position}%` }}>
      <span className="h-4 w-0.5 bg-ink-400" aria-hidden="true" />
      <span
        className={`mt-1 whitespace-nowrap text-[11px] font-medium text-ink-500 ${
          alignRight ? '-translate-x-full' : position === 0 ? '' : '-translate-x-1/2'
        }`}
      >
        {label}
      </span>
    </div>
  )
}

export type { ExposedSegment }
