// Numeric formatting helpers shared across the spend dashboard (M10).
//
// Cost numbers can span 6 orders of magnitude (a single tool call may cost
// $0.000045, a power user month $1,234). Pretty-printing to a fixed precision
// loses information at one end and looks noisy at the other, so we choose a
// precision based on the magnitude.

export function formatCostUsd(value: number): string {
  if (!Number.isFinite(value) || value === 0) return '$0.00'
  const abs = Math.abs(value)
  if (abs >= 1) return `$${value.toFixed(2)}`
  if (abs >= 0.01) return `$${value.toFixed(4)}`
  return `$${value.toFixed(6)}`
}

export function formatTokens(value: number): string {
  if (!Number.isFinite(value)) return '0'
  if (Math.abs(value) >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)}M`
  }
  if (Math.abs(value) >= 10_000) {
    return `${(value / 1_000).toFixed(1)}K`
  }
  return value.toLocaleString()
}

export function formatRequests(value: number): string {
  return Number.isFinite(value) ? value.toLocaleString() : '0'
}
