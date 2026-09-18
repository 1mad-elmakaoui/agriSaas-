/**
 * Expéditions — la fiche, puis la décision.
 *
 * L'analyse de risque est chargée séparément de la fiche, et volontairement :
 * elle fait tourner le moteur d'exposition sur trois itinéraires et dix options,
 * ce qui prend un peu de temps. La fiche s'affiche immédiatement ; l'analyse
 * arrive ensuite avec son propre état de chargement, plutôt que de retarder tout
 * l'écran.
 */
import { useState } from 'react'
import { api } from '@/lib/api'
import { useResource } from '@/lib/useResource'
import type { Shipment, TransportAlternative } from '@/lib/types'
import { dateTime, hoursFromNow, tonnes } from '@/lib/format'
import { PageHeader } from '@/components/Shell'
import { RouteMap } from '@/components/RouteMap'
import {
  AlternativesTable,
  ExposureTimeline,
  RecommendationPanel,
} from '@/components/logistics'
import { SourcesAndEvidence, WhyThisDecision } from '@/components/decision'
import { RecordDecision } from '@/components/RecordDecision'
import {
  ErrorNotice,
  Figure,
  Loading,
  NotDelivered,
  Panel,
  SeverityBadge,
} from '@/components/primitives'

export function ShipmentsPage() {
  const { data, loading, error } = useResource(() => api.shipments())
  const [selected, setSelected] = useState<string | null>(null)

  if (loading) return <Loading label="Chargement des expéditions…" />
  if (error) return <ErrorNotice message={error.message} remedy={error.remedy} />

  const shipments = data ?? []
  const current = shipments.find((s) => s.reference === selected) ?? shipments[0] ?? null

  return (
    <>
      <PageHeader title="Expéditions" subtitle={`${shipments.length} expédition(s)`} />
      {shipments.length === 0 ? (
        <Panel>
          <p className="text-sm text-ink-600">Aucune expédition enregistrée.</p>
        </Panel>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[320px,1fr]">
          <ul className="space-y-2">
            {shipments.map((shipment) => (
              <li key={shipment.reference}>
                <button
                  type="button"
                  onClick={() => setSelected(shipment.reference)}
                  className={`focusable w-full rounded-lg border px-4 py-3 text-left transition ${
                    current?.reference === shipment.reference
                      ? 'border-recommended bg-recommended/5'
                      : 'border-ink-200 bg-white hover:border-ink-300'
                  }`}
                >
                  <p className="text-sm font-semibold text-ink-900">{shipment.reference}</p>
                  <p className="mt-0.5 truncate text-sm text-ink-700">
                    {shipment.product_name_fr ?? 'Produit non renseigné'}
                  </p>
                  <p className="mt-0.5 text-xs text-ink-500">
                    {shipment.origin_site_fr} → {shipment.destination_site_fr}
                  </p>
                </button>
              </li>
            ))}
          </ul>
          {current && <ShipmentDetail shipment={current} />}
        </div>
      )}
    </>
  )
}

function ShipmentDetail({ shipment }: { shipment: Shipment }) {
  const late = shipment.hours_to_deadline < 0
  const tight = !late && shipment.hours_to_deadline <= 24

  return (
    <div className="space-y-5">
      <Panel
        title={`${shipment.reference} — ${shipment.product_name_fr ?? 'Produit non renseigné'}`}
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity="neutral" label={shipment.status_label_fr} />
            <SeverityBadge
              severity={late ? 'critical' : tight ? 'high' : 'low'}
              label={`Échéance ${hoursFromNow(shipment.hours_to_deadline)}`}
            />
            {shipment.requires_cold_chain && (
              <SeverityBadge severity="moderate" label="Chaîne du froid" />
            )}
          </div>
        }
      >
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Figure label="Volume" value={tonnes(shipment.volume_tonnes)} emphasis />
          <Figure label="Mode" value={shipment.transport_mode_label_fr} />
          <Figure label="Départ" value={dateTime(shipment.departure_at)} />
          <Figure label="Échéance de service" value={dateTime(shipment.sla_deadline_at)} />
        </div>
        <div className="mt-4 grid grid-cols-2 gap-4 border-t border-ink-200 pt-4 md:grid-cols-4">
          <Figure label="Origine" value={shipment.origin_site_fr} />
          <Figure label="Destination" value={shipment.destination_site_fr} />
          <Figure
            label="Parcelle d’origine"
            value={shipment.source_field_code}
            missingReason="Aucune parcelle d’origine enregistrée."
          />
        </div>
      </Panel>

      {shipment.risk_analysis_fr ? (
        <Panel title="Analyse de risque" subtitle="Exposition, alternatives, réacheminement">
          <NotDelivered>{shipment.risk_analysis_fr}</NotDelivered>
        </Panel>
      ) : (
        <RiskAnalysis reference={shipment.reference} />
      )}
    </div>
  )
}

function RiskAnalysis({ reference }: { reference: string }) {
  const { data, loading, error } = useResource(() => api.shipmentRisk(reference), [reference])
  const [selected, setSelected] = useState<TransportAlternative | null>(null)

  if (loading) {
    return (
      <Panel title="Analyse de risque">
        <Loading label="Évaluation des itinéraires et des options…" />
      </Panel>
    )
  }
  if (error) return <ErrorNotice message={error.message} remedy={error.remedy} />
  if (!data) return null

  // La recommandation pilote la carte : par défaut c'est elle qui est tracée,
  // et sélectionner une ligne du tableau trace ce corridor-là. La carte ne
  // modifie jamais la sélection en retour.
  return (
    <div className="space-y-5">
      <RecommendationPanel risk={data} />
      <div className="panel px-5 py-4">
        <RecordDecision domain="LOGISTICS" subjectId={reference} />
      </div>
      <RouteMap risk={data} selected={selected} />
      <ExposureTimeline risk={data} />
      <AlternativesTable
        risk={data}
        selectedId={selected?.id ?? null}
        onSelect={setSelected}
      />
      <div className="grid gap-5 xl:grid-cols-2">
        <WhyThisDecision decision={data.decision} />
        <SourcesAndEvidence decision={data.decision} />
      </div>
    </div>
  )
}
