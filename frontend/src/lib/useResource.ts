/**
 * Loading state with the three outcomes an interface actually has.
 *
 * `loading`, `data`, `error` — and the error carries the French message and its
 * remedy, so a screen never has to invent wording for a failure it did not
 * anticipate.
 */
import { useCallback, useEffect, useState } from 'react'
import { RequestFailed } from './api'

export interface Resource<T> {
  data: T | null
  loading: boolean
  error: { message: string; remedy: string | null } | null
  reload: () => void
}

export function useResource<T>(load: () => Promise<T>, deps: unknown[] = []): Resource<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<{ message: string; remedy: string | null } | null>(null)
  const [tick, setTick] = useState(0)

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(load, deps)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    run()
      .then((value) => {
        if (!cancelled) setData(value)
      })
      .catch((cause: unknown) => {
        if (cancelled) return
        if (cause instanceof RequestFailed) {
          setError({ message: cause.message, remedy: cause.remedyFr })
        } else {
          setError({
            message: 'Le serveur est momentanément injoignable.',
            remedy: 'Réessayez dans quelques instants.',
          })
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [run, tick])

  return { data, loading, error, reload: () => setTick((t) => t + 1) }
}
