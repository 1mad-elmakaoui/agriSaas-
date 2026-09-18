/**
 * Une jauge de quota — toujours avec son dénominateur.
 *
 * « 87 » ne dit rien. « 87 sur 100 questions » dit qu'il en reste treize, et
 * c'est la seule forme sur laquelle quelqu'un peut agir.
 *
 * Trois cas, trois rendus distincts, et aucun n'emprunte à un autre :
 *
 * * **plafonné et passant** — la barre et les deux nombres ;
 * * **illimité** — aucune barre. Une barre vide se lirait « rien de consommé »,
 *   une barre pleine « plafond atteint » ; les deux seraient faux, alors qu'il
 *   n'y a simplement pas de plafond à représenter ;
 * * **atteint** — le message du serveur et son remède, mot pour mot. L'interface
 *   ne rédige pas de refus : trois écrans qui le feraient chacun finiraient par
 *   en dire trois choses différentes.
 */
import type { Quota } from '@/lib/types'

/**
 * Accord en nombre. Singulier sous 2 — « 0 expédition » est correct en français.
 * Une unité en capitales est une abréviation (USD) et ne s'accorde pas.
 */
function agreed(unitFr: string, value: number): string {
  if (Math.abs(value) >= 2) return unitFr
  if (unitFr !== unitFr.toLowerCase() || !unitFr.endsWith('s')) return unitFr
  return unitFr.slice(0, -1)
}

function amount(value: number, unitFr: string): string {
  // Un décompte s'écrit sans décimale, une dépense en porte deux. « 100,0
  // questions » se lit comme une mesure ; c'en est un décompte.
  const formatted =
    unitFr === 'USD' || !Number.isInteger(value)
      ? value.toLocaleString('fr-FR', {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })
      : value.toLocaleString('fr-FR')
  return `${formatted} ${agreed(unitFr, value)}`.trim()
}

export function QuotaGauge({ quota }: { quota: Quota }) {
  const unlimited = quota.limit === null
  const fraction = quota.fraction ?? 0
  const tone = !quota.allowed
    ? 'bg-critical'
    : fraction >= 0.8
      ? 'bg-moderate'
      : 'bg-recommended'

  return (
    <div className="py-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <p className="text-sm text-ink-700">{quota.label_fr}</p>
        <p className="text-sm tabular-nums text-ink-900">
          {unlimited ? (
            <>
              <span className="font-medium">{amount(quota.used, quota.unit_fr)}</span>
              <span className="ml-1.5 text-xs text-ink-500">· sans plafond</span>
            </>
          ) : (
            <>
              <span className="font-medium">{amount(quota.used, quota.unit_fr)}</span>
              <span className="text-ink-500"> sur {amount(quota.limit ?? 0, quota.unit_fr)}</span>
            </>
          )}
        </p>
      </div>

      {!unlimited && (
        <div
          className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-ink-100"
          role="progressbar"
          aria-valuenow={Math.round(fraction * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={quota.label_fr}
        >
          <div
            className={`h-full rounded-full transition-all ${tone}`}
            style={{ width: `${Math.max(2, Math.round(fraction * 100))}%` }}
          />
        </div>
      )}

      {!quota.allowed && quota.message_fr && (
        <p className="mt-2 rounded-md bg-critical/10 px-3 py-2 text-xs text-ink-800">
          {quota.message_fr}
          {quota.remedy_fr && <span className="block text-ink-600">{quota.remedy_fr}</span>}
        </p>
      )}
    </div>
  )
}
