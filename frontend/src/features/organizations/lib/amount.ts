import { formatDisplayUsd } from '@/lib/utils/display-format'

/** A non-negative USD amount with at most 8 decimals (backend `Decimal(decimal_places=8)`). */
const AMOUNT = /^\d{1,12}(\.\d{1,8})?$/

export function isAmount(value: string): boolean {
  return AMOUNT.test(value.trim())
}

/** Normalized decimal string for a valid amount; `null` for an empty or invalid value. */
export function toAmount(value: string | null): string | null {
  if (value === null) return null
  const trimmed = value.trim()
  if (!isAmount(trimmed)) return null
  return String(Number(trimmed))
}

/** A decimal-string USD amount for display; `null` stays `null` (no limit). */
export function usd(value: string | null): string | null {
  return value === null ? null : formatDisplayUsd(Number(value))
}
