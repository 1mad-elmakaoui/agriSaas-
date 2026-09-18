/**
 * Le bouton qui archive une recommandation.
 *
 * Il n'envoie qu'un **sujet** — un code de parcelle, une référence
 * d'expédition — et le domaine. La décision est recalculée par le moteur au
 * moment de l'enregistrement : accepter une décision venue du navigateur
 * laisserait archiver n'importe quel chiffre sous le nom du moteur, et la
 * question « qu'avions-nous conseillé ? » perdrait toute valeur.
 *
 * Il propose, il n'exécute pas. Le libellé le dit, et le texte de confirmation
 * le répète : rien n'est irrigué, rien ne part, rien n'est engagé.
 */
import { useState } from 'react'
import { RequestFailed, api } from '@/lib/api'
import { Button, ErrorNotice } from './primitives'

export function RecordDecision({
  domain,
  subjectId,
}: {
  domain: 'IRRIGATION' | 'LOGISTICS'
  subjectId: string
}) {
  const [state, setState] = useState<'idle' | 'busy' | 'done'>('idle')
  const [error, setError] = useState<{ message: string; remedy: string | null } | null>(null)

  async function record() {
    setState('busy')
    setError(null)
    try {
      await api.recordRecommendation(domain, subjectId)
      setState('done')
    } catch (cause) {
      setState('idle')
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

  if (state === 'done') {
    return (
      <p className="text-xs text-low" role="status">
        Proposition enregistrée, en attente de décision. Aucune action n’a été
        déclenchée.
      </p>
    )
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button onClick={record} disabled={state === 'busy'}>
        {state === 'busy' ? 'Enregistrement…' : 'Enregistrer cette proposition'}
      </Button>
      {error && <ErrorNotice message={error.message} remedy={error.remedy} />}
    </div>
  )
}
