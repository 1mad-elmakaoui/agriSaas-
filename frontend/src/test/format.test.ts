/**
 * Number formatting, which is a correctness concern here rather than a
 * typographic one.
 */
import { describe, expect, it } from 'vitest'
import { mad, percent, volume } from '@/lib/format'

describe('French numbers', () => {
  it('never groups thousands with a period', () => {
    // « 3.936 MAD » reads as three point nine three six.
    expect(mad(3936)).not.toContain('.')
    expect(volume(2187)).not.toContain('.')
  })

  it('groups with the narrow no-break space the engines also use', () => {
    expect(mad(3936)).toBe('3 936 MAD')
  })

  it('renders an absent amount as an absence, never as zero', () => {
    expect(mad(null)).toBe('—')
    expect(mad(null)).not.toContain('0')
  })

  it('keeps one decimal on a moisture reading', () => {
    expect(percent(16.5)).toBe('16,5 %')
  })
})
