/**
 * Abonnement — le plan, les plafonds, et où en est la consommation.
 *
 * Aucun paiement n'est traité : un plan est **provisionné** par un
 * administrateur (§6). L'écran ne promet donc ni carte bancaire, ni facture, et
 * le dit plutôt que de laisser un bouton absent poser la question.
 *
 * La dépense du modèle porte son avertissement à côté du chiffre, pas dans une
 * page d'aide : elle est **dérivée** d'une grille tarifaire recopiée, et n'a été
 * rapprochée d'aucune facture. Un total présenté nu se lirait comme un montant
 * dû.
 */
import { useState } from 'react'
import { RequestFailed, api } from '@/lib/api'
import { useResource } from '@/lib/useResource'
import type { Plan } from '@/lib/types'
import { dateTime } from '@/lib/format'
import { PageHeader } from '@/components/Shell'
import { QuotaGauge } from '@/components/QuotaGauge'
import { Button, ErrorNotice, Loading, Panel } from '@/components/primitives'

function ceiling(value: number | null, unitFr: string): string {
  // « Illimité » plutôt que « 0 » ou qu'un tiret : l'absence de plafond est une
  // information, et c'est l'inverse de celle qu'un zéro donnerait.
  return value === null ? 'Illimité' : `${value.toLocaleString('fr-FR')} ${unitFr}`
}

function PlanCard({
  plan,
  current,
  onChoose,
  busy,
}: {
  plan: Plan
  current: boolean
  onChoose: (() => void) | null
  busy: boolean
}) {
  return (
    <div
      className={`rounded-md border px-4 py-3 ${
        current ? 'border-recommended bg-recommended/5' : 'border-ink-200 bg-white'
      }`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-sm font-semibold text-ink-900">{plan.name_fr}</p>
        {current && <span className="text-xs font-medium text-recommended">Plan actuel</span>}
      </div>
      <p className="mt-1 text-xs text-ink-600">{plan.description_fr}</p>
      <dl className="mt-3 space-y-1 text-xs text-ink-700">
        <div className="flex justify-between gap-2">
          <dt>Parcelles</dt>
          <dd className="tabular-nums">{ceiling(plan.max_fields, '')}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>Expéditions</dt>
          <dd className="tabular-nums">{ceiling(plan.max_shipments, '')}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>Questions au copilote</dt>
          <dd className="tabular-nums">
            {ceiling(plan.max_agent_messages_per_month, '/ mois')}
          </dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt>Questions d'analyse</dt>
          <dd className="tabular-nums">
            {ceiling(plan.max_analytics_queries_per_month, '/ mois')}
          </dd>
        </div>
      </dl>
      {onChoose && (
        <div className="mt-3">
          <Button onClick={onChoose} disabled={busy}>
            Choisir ce plan
          </Button>
        </div>
      )}
    </div>
  )
}

export function SubscriptionPage() {
  const subscription = useResource(() => api.subscription())
  const [error, setError] = useState<{ message: string; remedy: string | null } | null>(null)
  const [busy, setBusy] = useState(false)

  async function choose(code: string) {
    setError(null)
    setBusy(true)
    try {
      await api.changePlan(code)
      subscription.reload()
    } catch (cause) {
      if (cause instanceof RequestFailed) {
        setError({ message: cause.message, remedy: cause.remedyFr })
      } else {
        throw cause
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Abonnement"
        subtitle="Plan, plafonds et consommation du mois en cours."
      />

      {subscription.loading && <Loading label="Chargement de l'abonnement…" />}
      {subscription.error && (
        <ErrorNotice
          message={subscription.error.message}
          remedy={subscription.error.remedy}
        />
      )}
      {error && <ErrorNotice message={error.message} remedy={error.remedy} />}

      {subscription.data && (
        <div className="space-y-5">
          <Panel
            title={`Consommation — plan « ${subscription.data.plan.name_fr} »`}
            subtitle={`Période en cours depuis le ${dateTime(subscription.data.period_start)}.`}
          >
            <div className="divide-y divide-ink-100">
              <QuotaGauge quota={subscription.data.fields} />
              <QuotaGauge quota={subscription.data.shipments} />
              <QuotaGauge quota={subscription.data.agent_messages} />
              <QuotaGauge quota={subscription.data.analytics_queries} />
              <QuotaGauge quota={subscription.data.llm_spend} />
            </div>
            <p className="mt-3 border-t border-ink-100 pt-3 text-xs text-ink-500">
              {subscription.data.spend_notice_fr}
            </p>
          </Panel>

          <Panel
            title="Plans disponibles"
            subtitle="Aucun paiement n'est traité ici : un plan est provisionné par un administrateur de votre organisation."
          >
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {subscription.data.available_plans.map((plan) => (
                <PlanCard
                  key={plan.code}
                  plan={plan}
                  current={plan.code === subscription.data?.plan.code}
                  busy={busy}
                  onChoose={
                    plan.code === subscription.data?.plan.code
                      ? null
                      : () => void choose(plan.code)
                  }
                />
              ))}
            </div>
            <p className="mt-3 text-xs text-ink-500">
              Descendre de plan ne supprime aucune donnée : les parcelles existantes
              restent, et c'est la création suivante qui est refusée.
            </p>
          </Panel>
        </div>
      )}
    </>
  )
}
