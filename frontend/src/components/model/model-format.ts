/**
 * Token-price formatting helpers shared across model UI.
 *
 * Backend stores prices as USD-per-token (very small decimals). Users think in
 * USD-per-1M-tokens (LiteLLM convention), so we multiply by 1e6 on display.
 * Form inputs use the same 1M-tokens unit for editability.
 */

const PER_MILLION = 1_000_000

export function formatTokenPrice(perToken: number | null | undefined): string {
  if (perToken === null || perToken === undefined) return '—'
  const perMillion = Number(perToken) * PER_MILLION
  if (!Number.isFinite(perMillion)) return '—'
  // 6 decimals max, strip trailing zeros for tidy display
  const rounded = perMillion.toFixed(6).replace(/\.?0+$/, '')
  return `$${rounded} / 1M`
}

export function tokenPriceToPerMillion(perToken: number | null | undefined): number | '' {
  if (perToken === null || perToken === undefined) return ''
  const perMillion = Number(perToken) * PER_MILLION
  if (!Number.isFinite(perMillion)) return ''
  return Number(perMillion.toFixed(6))
}

export function perMillionToTokenPrice(
  perMillion: number | string | null | undefined,
): number | null {
  if (perMillion === null || perMillion === undefined || perMillion === '') {
    return null
  }
  const n = Number(perMillion)
  if (!Number.isFinite(n)) return null
  return n / PER_MILLION
}
