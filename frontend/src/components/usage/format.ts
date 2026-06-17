import {
  formatCompactCount,
  formatDisplayNumber,
  formatDisplayUsd,
} from '@/lib/utils/display-format'

export function formatCostUsd(value: number): string {
  if (!Number.isFinite(value) || value === 0) return '$0.00'
  const abs = Math.abs(value)
  if (abs >= 1) {
    return formatDisplayUsd(value, { maximumFractionDigits: 2, minimumFractionDigits: 2 })
  }
  if (abs >= 0.01) {
    return formatDisplayUsd(value, { maximumFractionDigits: 4, minimumFractionDigits: 4 })
  }
  return formatDisplayUsd(value, { maximumFractionDigits: 6, minimumFractionDigits: 6 })
}

export function formatTokens(value: number): string {
  if (!Number.isFinite(value)) return '0'
  return formatCompactCount(value, {
    millionFractionDigits: 2,
    minThousand: 10_000,
    thousandFractionDigits: 1,
  })
}

export function formatRequests(value: number): string {
  return formatDisplayNumber(value, { fallback: '0', maximumFractionDigits: 0 })
}
