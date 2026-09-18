/**
 * Recommandations — ce que le moteur a conseillé, et ce qui en a été fait.
 *
 * L'écran qui rend la boucle complète. Sans lui, une recommandation est un
 * affichage : produite, lue, oubliée. Avec lui, elle devient une trace que
 * quelqu'un a explicitement acceptée ou refusée, et sur laquelle on peut revenir
 * des mois plus tard.
 *
 * Deux règles que la page porte :
 *
 * **Un verdict se rend une fois.** Les boutons disparaissent dès que la décision
 * est prise. Permettre de « corriger » écraserait qui avait décidé quoi — ce que
 * cette table existe précisément pour conserver.
 *
 * **Le décompte n'est jamais un score seul.** « 50 % » sans dénominateur se lit
 * comme une performance ; « 1 acceptée sur 2 décidées » se vérifie à la main
 * depuis la liste juste en dessous.
 */
import { useState } from 'react'
import { RequestFailed, api } from '@/lib/api'
import { useResource } from '@/lib/useResource'
import type { DecidableVerdict, RecordedRecommendation } from '@/lib/types'
import { dateTime } from '@/lib/format'
import { PageHeader } from '@/components/Shell'
import {
  Button,
  ErrorNotice,
  Loading,
  NotDelivered,
  Panel,
  SourceChip,
  type Severity,
  SeverityBadge,
} from '@/components/primitives'

const VERDICT_SEVERITY: Record<string, Severity> = {
  PENDING: 'neutral',
  ACCEPTED: 'low',
  REJECTED: 'high',
  MODIFIED: 'moderate',
}

const CHOICES: { verdict: DecidableVerdict; label: string }[] = [
  { verdict: 'ACCEPTED', label: 'Accepter' },
  { verdict: 'MODIFIED', label: 'Appliquer avec modification' },
  { verdict: 'REJECTED', label: 'Refuser' },
]

export function RecommendationsPage() {
  const list = useResource(() => api.recommendations())
  const counts = useResource(() => api.recommendationCounts())
  const [error, setError] = useState<{ message: string; remedy: string | null } | null>(null)

  async function decide(id: string, verdict: DecidableVerdict) {
    setError(null)
    try {
      await api.decideRecommendation(id, verdict)
      list.reload()
      counts.reload()
    } catch (cause) {
      if (cause instanceof RequestFailed) {
        setError({ message: cause.message, remedy: cause.remedyFr })
      } else {
        setError({
          message: 'Le serveur est momentanément injoignable.',
          remedy: 'Réessayez dans quelques instants.',
        })
      }
    }
  }

  if (list.loading) return <Loading label="Chargement des recommandations…" />
  if (list.error) return <ErrorNotice message={list.error.message} remedy={list.error.remedy} />

  const rows = list.data ?? []
  const pending = rows.filter((r) => r.verdict === 'PENDING')
  const decided = rows.filter((r) => r.verdict !== 'PENDING')

  return (
    <>
      <PageHeader
        title="Recommandations"
        subtitle="Ce que les moteurs ont conseillé, et ce qui en a été fait"
      />

      {counts.data && (
        <div className="mb-5 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Count value={counts.data.pending} label="En attente de décision" emphasis />
          <Count value={counts.data.accepted} label="Acceptées" />
          <Count value={counts.data.rejected} label="Refusées" />
          <div className="panel px-5 py-4">
            <p className="tabular text-figure font-semibold text-ink-900">
              {counts.data.acceptance_rate === null
                ? '—'
                : `${Math.round(counts.data.acceptance_rate * 100)} %`}
            </p>
            <p className="mt-0.5 text-sm font-medium text-ink-700">Taux d’acceptation</p>
            {/* Le dénominateur, toujours : « 50 % » seul se lit comme une
                performance, « sur 2 décisions rendues » se vérifie. */}
            <p className="mt-0.5 text-xs text-ink-500">
              {counts.data.decided > 0
                ? `sur ${counts.data.decided} décision(s) rendue(s)`
                : counts.data.acceptance_label_fr}
            </p>
          </div>
        </div>
      )}

      {error && (
        <div className="mb-5">
          <ErrorNotice message={error.message} remedy={error.remedy} />
        </div>
      )}

      {rows.length === 0 ? (
        <Panel>
          <NotDelivered>
            Aucune recommandation enregistrée. Ouvrez une parcelle ou une expédition,
            puis enregistrez la proposition pour la retrouver ici.
          </NotDelivered>
        </Panel>
      ) : (
        <div className="space-y-5">
          {pending.length > 0 && (
            <Panel title="En attente de décision" subtitle={`${pending.length} proposition(s)`}>
              <ul className="space-y-4">
                {pending.map((row) => (
                  <li key={row.id}>
                    <Row recommendation={row} onDecide={decide} />
                  </li>
                ))}
              </ul>
            </Panel>
          )}
          {decided.length > 0 && (
            <Panel title="Décidées" subtitle={`${decided.length} recommandation(s)`}>
              <ul className="space-y-4">
                {decided.map((row) => (
                  <li key={row.id}>
                    <Row recommendation={row} />
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
      )}
    </>
  )
}

function Count({
  value,
  label,
  emphasis,
}: {
  value: number
  label: string
  emphasis?: boolean
}) {
  return (
    <div className="panel px-5 py-4">
      <p
        className={`tabular text-figure font-semibold ${
          emphasis && value > 0 ? 'text-high' : 'text-ink-900'
        }`}
      >
        {value}
      </p>
      <p className="mt-0.5 text-sm font-medium text-ink-700">{label}</p>
    </div>
  )
}

function Row({
  recommendation,
  onDecide,
}: {
  recommendation: RecordedRecommendation
  onDecide?: (id: string, verdict: DecidableVerdict) => void
}) {
  return (
    <div className="rounded-lg border border-ink-200 px-4 py-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-ink-900">
            {recommendation.subject_label_fr}
          </p>
          <p className="mt-0.5 text-sm text-ink-700">{recommendation.headline_fr}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <SeverityBadge
            severity={VERDICT_SEVERITY[recommendation.verdict] ?? 'neutral'}
            label={recommendation.verdict_label_fr}
          />
          <SourceChip
            state={recommendation.provenance.state}
            stateLabelFr={recommendation.provenance.state_label_fr}
            origin={recommendation.provenance.origin}
            originLabelFr={recommendation.provenance.origin_label_fr}
            sourceLabelFr={recommendation.provenance.source_label_fr}
          />
        </div>
      </div>

      {recommendation.rationale_fr && (
        <p className="mt-1.5 text-sm text-ink-600">{recommendation.rationale_fr}</p>
      )}

      <p className="mt-1.5 text-xs text-ink-500">
        {recommendation.domain_label_fr} · proposée le{' '}
        {dateTime(recommendation.created_at)}
        {recommendation.decided_at && recommendation.decided_by_name && (
          <>
            {' '}
            · {recommendation.verdict_label_fr.toLowerCase()} par{' '}
            {recommendation.decided_by_name} le {dateTime(recommendation.decided_at)}
          </>
        )}
      </p>

      {recommendation.decision_note_fr && (
        <p className="mt-1 text-sm italic text-ink-600">
          « {recommendation.decision_note_fr} »
        </p>
      )}

      {/* Les boutons n'existent que tant que rien n'est décidé. Permettre de
          « corriger » écraserait qui avait décidé quoi. */}
      {onDecide && recommendation.verdict === 'PENDING' && (
        <div className="mt-3 flex flex-wrap gap-2 border-t border-ink-100 pt-3">
          {CHOICES.map((choice) => (
            <Button
              key={choice.verdict}
              variant={choice.verdict === 'ACCEPTED' ? 'primary' : 'secondary'}
              onClick={() => onDecide(recommendation.id, choice.verdict)}
            >
              {choice.label}
            </Button>
          ))}
        </div>
      )}
    </div>
  )
}
