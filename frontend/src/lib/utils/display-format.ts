import { parseTimestamp } from './format-relative-time'

export const DEFAULT_DISPLAY_LOCALE = 'ko-KR'
export const DEFAULT_DISPLAY_TIME_ZONE = 'Asia/Seoul'
export const DEFAULT_EMPTY_DISPLAY = '-'

type DisplayDateValue = Date | number | string | null | undefined

type DisplayDateOptions = {
  readonly fallback?: string
  readonly format?: Intl.DateTimeFormatOptions
  readonly locale?: string
  readonly timeZone?: string
}

type DisplayNumberOptions = {
  readonly currency?: string
  readonly fallback?: string
  readonly locale?: string
  readonly maximumFractionDigits?: number
  readonly minimumFractionDigits?: number
  readonly style?: Intl.NumberFormatOptions['style']
  readonly useGrouping?: Intl.NumberFormatOptions['useGrouping']
}

type ResolvedDisplayDateOptions = {
  readonly fallback: string
  readonly format: Intl.DateTimeFormatOptions
  readonly locale: string | undefined
  readonly timeZone: string | undefined
}

type CompactCountOptions = {
  readonly millionFractionDigits?: number
  readonly millionSuffix?: string
  readonly minThousand?: number
  readonly thousandFractionDigits?: number
  readonly thousandSuffix?: string
}

const defaultDateFormat = {
  dateStyle: 'medium',
} satisfies Intl.DateTimeFormatOptions

const defaultDateTimeFormat = {
  dateStyle: 'medium',
  timeStyle: 'short',
} satisfies Intl.DateTimeFormatOptions

function parseDisplayDate(value: DisplayDateValue): Date | null {
  if (value === null || value === undefined || value === '') return null
  const date =
    typeof value === 'string'
      ? parseTimestamp(value)
      : value instanceof Date
        ? value
        : new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

export function formatDisplayDate(value: DisplayDateValue, options: DisplayDateOptions = {}) {
  return formatDisplayDateWith(value, {
    fallback: options.fallback ?? DEFAULT_EMPTY_DISPLAY,
    format: options.format ?? defaultDateFormat,
    locale: options.locale,
    timeZone: options.timeZone,
  })
}

export function formatDisplayDateTime(value: DisplayDateValue, options: DisplayDateOptions = {}) {
  return formatDisplayDateWith(value, {
    fallback: options.fallback ?? DEFAULT_EMPTY_DISPLAY,
    format: options.format ?? defaultDateTimeFormat,
    locale: options.locale,
    timeZone: options.timeZone,
  })
}

export function formatDisplayNumber(
  value: number | null | undefined,
  options: DisplayNumberOptions = {},
) {
  const fallback = options.fallback ?? DEFAULT_EMPTY_DISPLAY
  if (value === null || value === undefined || !Number.isFinite(value)) return fallback
  return new Intl.NumberFormat(
    options.locale ?? DEFAULT_DISPLAY_LOCALE,
    numberFormatOptions(options),
  ).format(value)
}

export function formatDisplayUsd(
  value: number | null | undefined,
  options: DisplayNumberOptions = {},
) {
  return formatDisplayNumber(value, {
    currency: 'USD',
    maximumFractionDigits: 4,
    style: 'currency',
    ...options,
    locale: options.locale ?? 'en-US',
  })
}

export function formatCompactCount(value: number, options: CompactCountOptions = {}) {
  const {
    millionFractionDigits = 1,
    millionSuffix = 'M',
    minThousand = 1_000,
    thousandFractionDigits = 1,
    thousandSuffix = 'K',
  } = options
  const abs = Math.abs(value)
  if (abs >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(millionFractionDigits)}${millionSuffix}`
  }
  if (abs >= minThousand) {
    return `${(value / 1_000).toFixed(thousandFractionDigits)}${thousandSuffix}`
  }
  return formatDisplayNumber(value, { fallback: '0', maximumFractionDigits: 0 })
}

export function formatDisplayBytes(bytes: number) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  if (bytes < 1024) return `${formatDisplayNumber(bytes, { maximumFractionDigits: 0 })} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDisplayDateWith(value: DisplayDateValue, options: ResolvedDisplayDateOptions) {
  const date = parseDisplayDate(value)
  if (!date) return options.fallback
  return new Intl.DateTimeFormat(options.locale ?? DEFAULT_DISPLAY_LOCALE, {
    timeZone: options.timeZone ?? DEFAULT_DISPLAY_TIME_ZONE,
    ...options.format,
  }).format(date)
}

function numberFormatOptions(options: DisplayNumberOptions): Intl.NumberFormatOptions {
  const formatOptions: Intl.NumberFormatOptions = {}
  if (options.currency !== undefined) formatOptions.currency = options.currency
  if (options.maximumFractionDigits !== undefined) {
    formatOptions.maximumFractionDigits = options.maximumFractionDigits
  }
  if (options.minimumFractionDigits !== undefined) {
    formatOptions.minimumFractionDigits = options.minimumFractionDigits
  }
  if (options.style !== undefined) formatOptions.style = options.style
  if (options.useGrouping !== undefined) formatOptions.useGrouping = options.useGrouping
  return formatOptions
}
