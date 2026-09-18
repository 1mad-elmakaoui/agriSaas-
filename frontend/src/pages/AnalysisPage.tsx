/**
 * Analyse — questions en langage naturel sur les données de l'organisation.
 *
 * Deux règles que cette page porte et qui ne sont pas cosmétiques.
 *
 * **Les lignes d'abord, la prose ensuite.** Comme pour le copilote : ce que
 * l'utilisateur lit en premier est le tableau renvoyé par la base. La phrase du
 * modèle est un résumé de ces lignes, affichée en dessous et nommée comme telle.
 * Un chiffre présent dans la phrase mais absent du tableau n'a aucune source.
 *
 * **Un échec montre sa requête.** Les tentatives ratées sont rendues avec leur
 * SQL et l'erreur exacte. Un exploitant qui ne voit qu'« impossible de répondre »
 * n'a rien à contester ; celui qui voit la requête peut dire « la colonne que tu
 * cherches s'appelle autrement ».
 */
import { useState } from 'react'
import { RequestFailed, api } from '@/lib/api'
import type { Analysis, Attempt } from '@/lib/types'
import { PageHeader } from '@/components/Shell'
import {
  Button,
  Disclosure,
  ErrorNotice,
  Loading,
  NotDelivered,
  Panel,
} from '@/components/primitives'

const SUGGESTIONS = [
  'Quelle est la surface totale par culture ?',
  'Quelles expéditions sont annulées ?',
  'Quelle parcelle a l’humidité du sol la plus basse ?',
]

const STATUS_LABEL_FR: Record<string, string> = {
  ok: 'Exécutée',
  invalid: 'Rejetée à la validation',
  plan_error: 'Rejetée par le planificateur',
  too_expensive: 'Trop coûteuse',
  execution_error: 'Erreur d’exécution',
  duplicate: 'Identique à une tentative précédente',
  refused: 'Refusée',
}

export function AnalysisPage() {
  const [question, setQuestion] = useState('')
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  const [error, setError] = useState<{ message: string; remedy: string | null } | null>(null)
  const [busy, setBusy] = useState(false)

  async function ask(text: string) {
    if (!text.trim()) return
    setBusy(true)
    setError(null)
    setAnalysis(null)
    try {
      setAnalysis(await api.analyse(text))
    } catch (cause) {
      if (cause instanceof RequestFailed) {
        setError({ message: cause.message, remedy: cause.remedyFr })
      } else {
        setError({
          message: 'Le serveur est momentanément injoignable.',
          remedy: 'Réessayez dans quelques instants.',
        })
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Analyse"
        subtitle="Posez une question sur vos données. La requête est vérifiée avant d’atteindre la base."
      />

      <Panel>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            void ask(question)
          }}
          className="flex flex-wrap gap-2"
        >
          <label htmlFor="analysis-question" className="sr-only">
            Votre question
          </label>
          <input
            id="analysis-question"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Combien d’eau avons-nous utilisée par ferme ce mois-ci ?"
            className="focusable min-w-0 flex-1 rounded-md border border-ink-300 px-3 py-2 text-sm"
          />
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Analyse…' : 'Interroger'}
          </Button>
        </form>
        <ul className="mt-3 flex flex-wrap gap-2">
          {SUGGESTIONS.map((suggestion) => (
            <li key={suggestion}>
              <button
                type="button"
                onClick={() => {
                  setQuestion(suggestion)
                  void ask(suggestion)
                }}
                className="focusable rounded-full border border-ink-200 px-3 py-1 text-xs text-ink-600 hover:bg-ink-50"
              >
                {suggestion}
              </button>
            </li>
          ))}
        </ul>
      </Panel>

      {busy && (
        <div className="mt-5">
          <Loading label="Génération, vérification, puis exécution en lecture seule…" />
        </div>
      )}
      {error && (
        <div className="mt-5">
          <ErrorNotice message={error.message} remedy={error.remedy} />
        </div>
      )}
      {analysis && (
        <div className="mt-5">
          <AnalysisView analysis={analysis} />
        </div>
      )}
    </>
  )
}

function AnalysisView({ analysis }: { analysis: Analysis }) {
  if (analysis.refused) {
    return (
      <div className="space-y-5">
        <ErrorNotice
          message={analysis.text_fr}
          remedy={analysis.refusal_reason_fr}
        />
        <AttemptTrace attempts={analysis.attempts} />
      </div>
    )
  }

  return (
    <div className="space-y-5">
      {analysis.row_count === 0 && analysis.sql && (
        <NotDelivered>
          La requête s’est exécutée et n’a renvoyé aucune ligne. Ce n’est pas une
          erreur : il n’y a rien qui corresponde à cette question dans vos données.
        </NotDelivered>
      )}

      {analysis.row_count > 0 && (
        <Panel
          title="Résultat"
          subtitle={`${analysis.row_count} ligne(s)`}
          actions={
            analysis.truncated ? (
              <span className="rounded bg-moderate/10 px-2 py-1 text-xs font-medium text-moderate">
                Résultat partiel
              </span>
            ) : undefined
          }
        >
          {analysis.truncated && analysis.truncation_reason_fr && (
            <p className="mb-3 text-sm text-moderate">{analysis.truncation_reason_fr}</p>
          )}
          <ResultTable analysis={analysis} />
        </Panel>
      )}

      <Panel
        title="Lecture du résultat"
        subtitle="Texte rédigé par le modèle à partir des seules lignes ci-dessus"
      >
        <p className="whitespace-pre-line text-sm leading-relaxed text-ink-600">
          {analysis.text_fr}
        </p>
        <p className="mt-3 border-t border-ink-200 pt-2 text-xs text-ink-500">
          invite {analysis.prompt_version} · {analysis.attempts.length} tentative(s) ·{' '}
          {analysis.cost_label_fr}
        </p>
      </Panel>

      <AttemptTrace attempts={analysis.attempts} />
    </div>
  )
}

function ResultTable({ analysis }: { analysis: Analysis }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-ink-200 text-left text-[11px] uppercase tracking-wider text-ink-500">
            {analysis.columns.map((column) => (
              <th key={column} className="py-2 pr-4 font-semibold">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">
          {analysis.rows.map((row, index) => (
            <tr key={index}>
              {analysis.columns.map((column) => (
                <td key={column} className="tabular py-2 pr-4 text-ink-800">
                  {renderCell(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * Une cellule est du **texte**, toujours.
 *
 * Ces valeurs viennent d'une base de données, qui est pleine de texte saisi par
 * des utilisateurs. React échappe par défaut ; ce qui compte ici est de ne jamais
 * introduire d'exception à cette règle, et un test balaie l'arbre pour s'en
 * assurer.
 */
function renderCell(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') {
    return new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 2 }).format(value)
  }
  if (typeof value === 'boolean') return value ? 'oui' : 'non'
  return String(value)
}

/**
 * La trace des tentatives, avec le SQL — y compris celui qui a échoué.
 *
 * Repliée quand tout s'est bien passé, ouverte quand il y a eu des reprises :
 * c'est précisément le moment où quelqu'un veut savoir ce qui a été tenté.
 */
function AttemptTrace({ attempts }: { attempts: Attempt[] }) {
  if (attempts.length === 0) return null
  const hadRetries = attempts.length > 1 || attempts.some((a) => a.status !== 'ok')

  return (
    <Disclosure
      summary="Voir les requêtes"
      count={attempts.length}
      defaultOpen={hadRetries}
    >
      <ol className="space-y-3">
        {attempts.map((attempt) => (
          <li key={attempt.index}>
            <div className="flex flex-wrap items-baseline gap-2">
              <span className="text-sm font-medium text-ink-800">
                Tentative {attempt.index}
              </span>
              <span
                className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${
                  attempt.status === 'ok'
                    ? 'bg-low/10 text-low'
                    : attempt.status === 'refused'
                      ? 'bg-critical/10 text-critical'
                      : 'bg-ink-100 text-ink-600'
                }`}
              >
                {STATUS_LABEL_FR[attempt.status] ?? attempt.status}
              </span>
              {attempt.duration_ms !== null && (
                <span className="text-xs text-ink-500">{attempt.duration_ms} ms</span>
              )}
            </div>
            <pre className="mt-1 overflow-x-auto rounded border border-ink-200 bg-white px-3 py-2 text-xs text-ink-700">
              {attempt.sql}
            </pre>
            {attempt.issues_fr.length > 0 && (
              <ul className="mt-1 space-y-0.5">
                {attempt.issues_fr.map((issue, index) => (
                  <li key={index} className="text-xs text-ink-600">
                    {issue}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ol>
    </Disclosure>
  )
}
