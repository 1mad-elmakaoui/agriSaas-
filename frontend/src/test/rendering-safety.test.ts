/**
 * The rendering ban, asserted rather than trusted.
 *
 * `txtsql` carried this test against a vanilla page that the React port removes,
 * so this is **written**, not inherited — the principle is what survived. A crop
 * or supplier name containing `<img src=x onerror=…>` is a realistic row in any
 * system that accepts typed input, and this product accepts typed input from
 * farmers.
 *
 * React escapes by default. The only ways to lose that escaping are the four
 * names below, so the guard is a sweep of the source tree rather than a
 * component-by-component check that a new component would silently escape.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const SOURCE_ROOT = dirname(dirname(fileURLToPath(import.meta.url)))

const FORBIDDEN = [
  'dangerouslySetInnerHTML',
  '.innerHTML',
  'insertAdjacentHTML',
  'document.write',
]

function sourceFiles(directory: string): string[] {
  return readdirSync(directory).flatMap((entry) => {
    const path = join(directory, entry)
    if (statSync(path).isDirectory()) return sourceFiles(path)
    return /\.tsx?$/.test(entry) ? [path] : []
  })
}

describe('rendering safety', () => {
  const files = sourceFiles(SOURCE_ROOT).filter((f) => !f.endsWith('rendering-safety.test.ts'))

  it('sweeps a non-trivial tree', () => {
    expect(files.length).toBeGreaterThan(10)
  })

  for (const name of FORBIDDEN) {
    it(`never uses ${name}`, () => {
      const offenders = files.filter((file) => readFileSync(file, 'utf8').includes(name))
      expect(offenders).toEqual([])
    })
  }

  it('builds map popups from text nodes, never from markup', () => {
    // The map is the one place where content leaves React's escaping, because
    // MapLibre owns those DOM nodes. It must use textContent.
    const map = readFileSync(join(SOURCE_ROOT, 'components/OperationalMap.tsx'), 'utf8')
    expect(map).toContain('textContent')
    expect(map).not.toContain('setHTML')
  })
})
