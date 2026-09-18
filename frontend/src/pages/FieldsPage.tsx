/**
 * Parcelles — the list, the map, and the recommendation for the selected one.
 *
 * The recommendation drives the map: selecting a parcelle flies to it and its
 * marker takes the colour of its recommendation. A parcelle whose recommendation
 * cannot be computed stays in the list and on the map, in grey, with the reason.
 */
import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '@/lib/api'
import { useResource } from '@/lib/useResource'
import type { Field, Recommendation } from '@/lib/types'
import { area, depth, mad, percent, rate, volume } from '@/lib/format'
import { PageHeader } from '@/components/Shell'
import { OperationalMap } from '@/components/OperationalMap'
import { SourcesAndEvidence, WhyThisDecision } from '@/components/decision'
import { RecordDecision } from '@/components/RecordDecision'
import {
  ErrorNotice,
  Figure,
  Loading,
  NotDelivered,
  Panel,
  STRESS_SEVERITY,
  SeverityBadge,
  SourceChip,
} from '@/components/primitives'

const RECOMMENDATION_SEVERITY: Record<Recommendation, 'low' | 'moderate' | 'high' | 'critical'> = {
  IRRIGATE: 'high',
  MONITOR: 'moderate',
  NO_IRRIGATION: 'low',
  POSTPONE_RAIN: 'low',
}

export function FieldsPage() {
  const { code } = useParams<{ code: string }>()
  const navigate = useNavigate()
  const fields = useResource(() => api.fields())
  const [selected, setSelected] = useState<string | null>(code ?? null)

  useEffect(() => {
    if (code) setSelected(code)
  }, [code])

  const list = useMemo(() => fields.data ?? [], [fields.data])

  useEffect(() => {
    if (selected === null && list.length > 0) {
      const first = list.find((f) => f.blocked_reason_fr === null) ?? list[0]
      if (first) setSelected(first.code)
    }
  }, [list, selected])

  if (fields.loading) return <Loading label="Chargement des parcelles…" />
  if (fields.error) return <ErrorNotice message={fields.error.message} remedy={fields.error.remedy} />

  return (
    <>
      <PageHeader
        title="Parcelles"
        subtitle={`${list.length} parcelle(s) suivie(s)`}
      />
      <div className="grid gap-5 lg:grid-cols-[320px,1fr]">
        <div className="space-y-3">
          <ul className="space-y-2">
            {list.map((field) => (
              <li key={field.code}>
                <FieldCard
                  field={field}
                  selected={selected === field.code}
                  onSelect={() => {
                    setSelected(field.code)
                    navigate(`/parcelles/${field.code}`)
                  }}
                />
              </li>
            ))}
          </ul>
        </div>

        <div className="space-y-5">
          <OperationalMap
            fields={list}
            selectedCode={selected}
            onSelect={(next) => {
              setSelected(next)
              navigate(`/parcelles/${next}`)
            }}
          />
          {selected && <FieldRecommendation code={selected} fields={list} />}
        </div>
      </div>
    </>
  )
}

function FieldCard({
  field,
  selected,
  onSelect,
}: {
  field: Field
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`focusable w-full rounded-lg border px-4 py-3 text-left transition ${
        selected
          ? 'border-recommended bg-recommended/5'
          : 'border-ink-200 bg-white hover:border-ink-300'
      }`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-sm font-semibold text-ink-900">{field.code}</p>
        <p className="tabular text-xs text-ink-500">{area(field.area_ha)}</p>
      </div>
      <p className="mt-0.5 truncate text-sm text-ink-700">{field.name_fr}</p>
      <p className="mt-0.5 text-xs text-ink-500">
        {field.crop_name_fr ?? 'Culture non renseignée'} · {field.site_name_fr}
      </p>
      {field.blocked_reason_fr ? (
        <p className="mt-2 text-xs text-ink-500">{field.blocked_reason_fr}</p>
      ) : (
        field.moisture && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span className="tabular text-xs font-medium text-ink-700">
              Humidité {percent(field.moisture.value_pct)}
            </span>
            <SourceChip
              state={field.moisture.state}
              stateLabelFr={field.moisture.state_label_fr}
              origin={field.moisture.origin}
              originLabelFr={field.moisture.origin_label_fr}
            />
          </div>
        )
      )}
    </button>
  )
}

function FieldRecommendation({ code, fields }: { code: string; fields: Field[] }) {
  const field = fields.find((f) => f.code === code)
  const blocked = field?.blocked_reason_fr ?? null
  const { data, loading, error } = useResource(
    () => api.irrigation(code),
    [code, blocked],
  )

  if (blocked) {
    return (
      <Panel title={`${code} — ${field?.name_fr ?? ''}`}>
        <NotDelivered>
          {blocked} Aucune recommandation n’est produite tant que cette information
          manque ; aucune valeur n’a été estimée à sa place.
        </NotDelivered>
      </Panel>
    )
  }
  if (loading) return <Loading label="Calcul de la recommandation…" />
  if (error) return <ErrorNotice message={error.message} remedy={error.remedy} />
  if (!data) return null

  return (
    <div className="space-y-5">
      <Panel
        title={`${data.field_code} — ${data.field_name_fr}`}
        actions={
          <div className="flex items-center gap-2">
            <SeverityBadge
              severity={RECOMMENDATION_SEVERITY[data.recommendation]}
              label={data.recommendation_label_fr}
            />
            <SeverityBadge
              severity={STRESS_SEVERITY[data.stress_level]}
              label={data.stress_label_fr}
            />
          </div>
        }
      >
        <p className="text-sm text-ink-700">{data.headline_fr}</p>

        <div className="mt-4 grid grid-cols-2 gap-4 border-t border-ink-200 pt-4 md:grid-cols-4">
          <Figure label="Volume" value={volume(data.water_volume_m3)} emphasis />
          <Figure label="Dose nette" value={depth(data.net_requirement_mm)} />
          <Figure
            label="Durée"
            value={data.duration_label_fr}
            missingReason={data.decision.unavailable_fr.duree}
          />
          <Figure
            label="Coût estimé"
            value={data.estimated_cost_mad === null ? null : mad(data.estimated_cost_mad)}
            missingReason={data.decision.unavailable_fr.cout}
          />
        </div>

        <div className="mt-4 grid grid-cols-2 gap-4 border-t border-ink-200 pt-4 md:grid-cols-4">
          <Figure label="ET0" value={rate(data.et0_mm_day)} />
          <Figure label="ETc" value={rate(data.etc_mm_day)} />
          <Figure
            label="Fiabilité"
            value={data.reliability_label_fr}
            missingReason="Fiabilité non calculée."
          />
        </div>

        <div className="mt-4 border-t border-ink-200 pt-4">
          <RecordDecision domain="IRRIGATION" subjectId={data.field_code} />
        </div>
      </Panel>

      <div className="grid gap-5 xl:grid-cols-2">
        <WhyThisDecision decision={data.decision} />
        <SourcesAndEvidence decision={data.decision} />
      </div>
    </div>
  )
}
