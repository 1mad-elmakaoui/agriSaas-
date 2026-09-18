/**
 * Locale plumbing.
 *
 * Small on purpose. What it must get right is the part that is expensive to
 * retrofit: `lang` and `dir` on the document element, so that the day an Arabic
 * catalogue exists the layout, the map controls and the charts are already
 * being told which way the page runs.
 */
import type { ReactNode } from 'react'
import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import type { Locale, MessageKey } from './messages'
import { direction, translate } from './messages'

interface LocaleValue {
  locale: Locale
  setLocale: (locale: Locale) => void
  t: (key: MessageKey) => string
}

const LocaleContext = createContext<LocaleValue | null>(null)

export function LocaleProvider({
  children,
  initial = 'fr',
}: {
  children: ReactNode
  initial?: Locale
}) {
  const [locale, setLocale] = useState<Locale>(initial)

  useEffect(() => {
    document.documentElement.lang = locale
    document.documentElement.dir = direction(locale)
  }, [locale])

  const value = useMemo<LocaleValue>(
    () => ({ locale, setLocale, t: (key) => translate(locale, key) }),
    [locale],
  )
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>
}

export function useLocale(): LocaleValue {
  const value = useContext(LocaleContext)
  if (value === null) {
    throw new Error('useLocale doit être utilisé à l’intérieur de LocaleProvider.')
  }
  return value
}

export type { Locale, MessageKey }
