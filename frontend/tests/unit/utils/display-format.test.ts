import { describe, expect, it } from 'vitest'

import {
  formatCompactCount,
  formatDisplayBytes,
  formatDisplayDate,
  formatDisplayDateTime,
  formatDisplayNumber,
  formatDisplayUsd,
} from '@/lib/utils/display-format'

describe('display-format helpers', () => {
  it('uses explicit fallbacks for empty or invalid dates', () => {
    expect(formatDisplayDate(null)).toBe('-')
    expect(formatDisplayDate('not-a-date', { fallback: '' })).toBe('')
  })

  it('normalizes timezone-naive backend timestamps as UTC', () => {
    expect(formatDisplayDateTime('2026-06-17T00:00:00')).toBe(
      formatDisplayDateTime('2026-06-17T00:00:00Z'),
    )
  })

  it('formats display numbers with a stable default locale', () => {
    expect(formatDisplayNumber(1234567)).toBe('1,234,567')
    expect(formatDisplayNumber(Number.NaN, { fallback: '0' })).toBe('0')
  })

  it('formats compact counts for token surfaces', () => {
    expect(formatCompactCount(1234, { thousandSuffix: 'k' })).toBe('1.2k')
    expect(formatCompactCount(1200000, { millionFractionDigits: 2 })).toBe('1.20M')
  })

  it('formats bytes and USD values for product metrics', () => {
    expect(formatDisplayBytes(1536)).toBe('1.5 KB')
    expect(
      formatDisplayUsd(0.000045, {
        maximumFractionDigits: 6,
        minimumFractionDigits: 6,
      }),
    ).toBe('$0.000045')
  })
})
