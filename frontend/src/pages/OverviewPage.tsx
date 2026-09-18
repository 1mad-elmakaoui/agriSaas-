/**
 * Vue générale.
 *
 * Counts, never a composite index. Each number here can be checked by hand from
 * another screen; a single « indice de santé » would be a figure nobody can
 * contest, and therefore a figure nobody should believe.
 *
 * The undelivered capabilities are listed on the home page on purpose. A
 * platform that shows five working tiles and stays silent about the rest reads
 * as complete.
 */
import { Link } from 'react-router-dom'
import { api } from '@/lib/api'
import { useResource } from '@/lib/useResource'
import { PageHeader } from '@/components/Shell'
import { ErrorNotice, Loading, NotDelivered, Panel } from '@/components/primitives'

function Count({
  value,
  label,
  detail,
  to,
  emphasis,
}: {
  value: number
  label: string
  detail?: string
  to?: string
  emphasis?: boolean
}) {
  const body = (
    <>
      <p className={`tabular text-figure font-semibold ${emphasis && value > 0 ? 'text-high' : 'text-ink-900'}`}>
        {value}
      </p>
      <p className="mt-0.5 text-sm font-medium text-ink-700">{label}</p>
      {detail && <p className="mt-0.5 text-xs text-ink-500">{detail}</p>}
    </>
  )
  return (
    <div className="panel px-5 py-4">
      {to ? (
        <Link to={to} className="focusable block rounded">
          {body}
        </Link>
      ) : (
        body
      )}
    </div>
  )
}

export function OverviewPage() {
  const { data, loading, error } = useResource(() => api.overview())

  if (loading) return <Loading label="Chargement de la vue générale…" />
  if (error) return <ErrorNotice message={error.message} remedy={error.remedy} />
  if (!data) return null

  return (
    <>
      <PageHeader
        title="Vue générale"
        subtitle={`${data.tenant_name_fr} — situation du jour`}
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Count
          value={data.fields_to_irrigate}
          label="Parcelles à irriguer"
          detail="Recommandation calculée par le moteur FAO-56"
          to="/parcelles"
          emphasis
        />
        <Count value={data.fields_total} label="Parcelles suivies" to="/parcelles" />
        <Count
          value={data.fields_blocked}
          label="Parcelles sans recommandation"
          detail="Une information manque pour calculer"
          to="/parcelles"
        />
        <Count
          value={data.shipments_due_within_24h}
          label="Échéances sous 24 h"
          detail={`${data.shipments_in_transit} expédition(s) en transit`}
          to="/expeditions"
          emphasis
        />
      </div>

      <div className="mt-6">
        <Panel
          title="Ce que cette installation ne fait pas encore"
          subtitle="Annoncé par le serveur, pas écrit dans l’interface"
        >
          <NotDelivered>
            <ul className="space-y-1">
              {data.not_delivered_fr.map((line) => (
                <li key={line}>· {line}</li>
              ))}
            </ul>
          </NotDelivered>
        </Panel>
      </div>
    </>
  )
}
