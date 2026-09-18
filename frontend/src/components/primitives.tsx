/**
 * The small pieces every screen is built from.
 *
 * `SourceChip` is the one that carries the product. Every number on every screen
 * is rendered next to one, because 27 % measured by a probe and 27 % from the
 * demonstration dataset are the same digits in the same column, and only this
 * pair separates them.
 */
import type { ReactNode } from 'react'
import { useId, useState } from 'react'
import type { DataOrigin, DataState, StressLevel } from '@/lib/types'

/** Severity → semantic colour. The only place hues are chosen. */
const SEVERITY = {
  low: { dot: 'bg-low', text: 'text-low', ring: 'ring-low/30', bg: 'bg-low/8' },
  moderate: { dot: 'bg-moderate', text: 'text-moderate', ring: 'ring-moderate/30', bg: 'bg-moderate/8' },
  high: { dot: 'bg-high', text: 'text-high', ring: 'ring-high/30', bg: 'bg-high/8' },
  critical: { dot: 'bg-critical', text: 'text-critical', ring: 'ring-critical/30', bg: 'bg-critical/8' },
  neutral: { dot: 'bg-ink-400', text: 'text-ink-600', ring: 'ring-ink-300', bg: 'bg-ink-100' },
} as const

export type Severity = keyof typeof SEVERITY

export const STRESS_SEVERITY: Record<StressLevel, Severity> = {
  normal: 'low',
  moderate: 'moderate',
  high: 'high',
  critical: 'critical',
}

export function Panel({
  title,
  subtitle,
  actions,
  children,
}: {
  title?: string
  subtitle?: string
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <section className="panel">
      {(title || actions) && (
        <header className="flex items-start justify-between gap-4 border-b border-ink-200 px-5 py-3.5">
          <div>
            {title && <h2 className="text-sm font-semibold text-ink-800">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-500">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  )
}

export function SeverityBadge({
  severity,
  label,
}: {
  severity: Severity
  label: string
}) {
  const tone = SEVERITY[severity]
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset ${tone.bg} ${tone.text} ${tone.ring}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} aria-hidden="true" />
      {label}
    </span>
  )
}

/**
 * The provenance chip.
 *
 * Both axes are shown, never collapsed into one word. « Simulé » alone would
 * not say whether the number came from the demonstration dataset or from an
 * offline provider standing in for a real one, and those are different facts.
 */
export function SourceChip({
  state,
  stateLabelFr,
  origin,
  originLabelFr,
  sourceLabelFr,
}: {
  state: DataState
  stateLabelFr: string
  origin: DataOrigin
  originLabelFr: string
  sourceLabelFr?: string | null
}) {
  // A measurement and a fabricated value must not look alike at a glance.
  const tone =
    state === 'OBSERVED'
      ? 'border-low/40 bg-low/8 text-low'
      : state === 'SIMULATED'
        ? 'border-ink-300 bg-ink-100 text-ink-600'
        : 'border-ink-200 bg-white text-ink-600'

  const title = sourceLabelFr
    ? `${stateLabelFr} · ${originLabelFr} · ${sourceLabelFr}`
    : `${stateLabelFr} · ${originLabelFr}`

  return (
    <span
      title={title}
      data-state={state}
      data-origin={origin}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium ${tone}`}
    >
      {stateLabelFr}
      <span className="text-ink-400" aria-hidden="true">
        ·
      </span>
      <span className="font-normal">{originLabelFr}</span>
    </span>
  )
}

/**
 * State plus named source, for evidence items.
 *
 * Deliberately *not* `SourceChip`: an evidence item carries a `DataState` and a
 * source name but no `DataOrigin`. Reusing the two-axis chip here would mean
 * inventing an origin to fill the slot, which is the exact failure the two axes
 * exist to prevent.
 */
export function StateChip({
  state,
  stateLabelFr,
  sourceLabelFr,
}: {
  state: DataState
  stateLabelFr: string
  sourceLabelFr?: string | null
}) {
  const tone =
    state === 'OBSERVED'
      ? 'border-low/40 bg-low/8 text-low'
      : state === 'SIMULATED'
        ? 'border-ink-300 bg-ink-100 text-ink-600'
        : 'border-ink-200 bg-white text-ink-600'
  return (
    <span
      data-state={state}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-medium ${tone}`}
    >
      {stateLabelFr}
      {sourceLabelFr && (
        <>
          <span className="text-ink-400" aria-hidden="true">
            ·
          </span>
          <span className="font-normal">{sourceLabelFr}</span>
        </>
      )}
    </span>
  )
}

/**
 * A figure with its unit and provenance.
 *
 * `value === null` renders the reason instead of the number. There is no code
 * path here that shows a dash and leaves the user guessing why.
 */
export function Figure({
  label,
  value,
  missingReason,
  chip,
  emphasis,
}: {
  label: string
  value: string | null
  missingReason?: string | null
  chip?: ReactNode
  emphasis?: boolean
}) {
  return (
    <div>
      <p className="panel-title">{label}</p>
      {value !== null ? (
        <p
          className={`tabular mt-1 font-semibold text-ink-900 ${
            emphasis ? 'text-figure' : 'text-lg'
          }`}
        >
          {value}
        </p>
      ) : (
        <p className="mt-1 text-sm text-ink-500">
          {missingReason ?? 'Non disponible.'}
        </p>
      )}
      {chip && <div className="mt-1.5">{chip}</div>}
    </div>
  )
}

/** A disclosure. Technical detail lives behind one; never on the surface. */
export function Disclosure({
  summary,
  count,
  children,
  defaultOpen = false,
}: {
  summary: string
  count?: number
  children: ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  const id = useId()
  return (
    <div className="rounded-md border border-ink-200 bg-ink-50/60">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={id}
        className="focusable flex w-full items-center justify-between gap-3 px-3.5 py-2.5 text-left text-sm font-medium text-ink-700"
      >
        <span>
          {summary}
          {count !== undefined && (
            <span className="ml-2 rounded bg-ink-200 px-1.5 py-0.5 text-[11px] font-semibold text-ink-600">
              {count}
            </span>
          )}
        </span>
        <span className="text-ink-400" aria-hidden="true">
          {open ? '−' : '+'}
        </span>
      </button>
      {open && (
        <div id={id} className="border-t border-ink-200 px-3.5 py-3">
          {children}
        </div>
      )}
    </div>
  )
}

export function Button({
  children,
  onClick,
  variant = 'secondary',
  type = 'button',
  disabled,
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'primary' | 'secondary'
  type?: 'button' | 'submit'
  disabled?: boolean
}) {
  const tone =
    variant === 'primary'
      ? 'bg-recommended text-white hover:bg-recommended/90 disabled:bg-ink-300'
      : 'border border-ink-300 bg-white text-ink-700 hover:bg-ink-50 disabled:text-ink-400'
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`focusable rounded-md px-3 py-1.5 text-sm font-medium transition ${tone}`}
    >
      {children}
    </button>
  )
}

/**
 * A stated absence.
 *
 * Used wherever a capability is not delivered. It is deliberately plain and
 * unmissable: an empty panel would read as a loading state or a bug, and the
 * user would wait for something that is never coming.
 */
export function NotDelivered({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-ink-300 bg-ink-50 px-4 py-3">
      {/* A div, not a p: callers legitimately pass a list of missing
          capabilities, and a <ul> inside a <p> is invalid HTML that browsers
          silently restructure — which then breaks the styling in one browser
          and not another. */}
      <div className="text-sm text-ink-600">{children}</div>
    </div>
  )
}

export function ErrorNotice({
  message,
  remedy,
}: {
  message: string
  remedy?: string | null
}) {
  return (
    <div
      role="alert"
      className="rounded-md border border-critical/30 bg-critical/8 px-4 py-3"
    >
      <p className="text-sm font-medium text-critical">{message}</p>
      {remedy && <p className="mt-1 text-sm text-ink-600">{remedy}</p>}
    </div>
  )
}

export function Loading({ label }: { label: string }) {
  return (
    <p role="status" className="text-sm text-ink-500">
      {label}
    </p>
  )
}
