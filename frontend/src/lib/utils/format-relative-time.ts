const TIMEZONE = 'Asia/Seoul'
const LOCALE = 'ko-KR'

const dayKeyFmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: TIMEZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

const todayTimeFmt = new Intl.DateTimeFormat(LOCALE, {
  timeZone: TIMEZONE,
  hour: 'numeric',
  minute: '2-digit',
})

const monthDayFmt = new Intl.DateTimeFormat(LOCALE, {
  timeZone: TIMEZONE,
  month: 'numeric',
  day: 'numeric',
})

const longDateFmt = new Intl.DateTimeFormat(LOCALE, {
  timeZone: TIMEZONE,
  year: 'numeric',
  month: 'long',
  day: 'numeric',
})

const mediumDateFmt = new Intl.DateTimeFormat(LOCALE, {
  timeZone: TIMEZONE,
  year: 'numeric',
  month: 'short',
  day: 'numeric',
})

function dayKey(d: Date): string {
  return dayKeyFmt.format(d)
}

/**
 * 백엔드 timestamp(timezone-naive UTC)를 UTC로 가정해 Date로 파싱.
 * 'Z' / '+09:00' 같은 timezone 표시가 이미 있으면 그대로 사용.
 */
export function parseTimestamp(value: Date | string): Date {
  if (value instanceof Date) return value
  const hasTz = /Z|[+-]\d{2}:?\d{2}$/.test(value)
  return new Date(hasTz ? value : value + 'Z')
}

/**
 * 짧은 한국어 상대 시간 (KST 기준):
 * - 오늘  → "오전 10:30"
 * - 어제  → yesterdayLabel
 * - 그 외 → "5. 22."
 *
 * use-intl `dateTime`이 옵션의 `timeZone`을 일관되게 적용하지 않는 케이스가
 * 있어 환경 무관 동작을 위해 Intl.DateTimeFormat을 직접 사용한다.
 */
export function formatRelativeShort(
  date: Date | string,
  yesterdayLabel: string,
  now: Date = new Date(),
): string {
  const d = parseTimestamp(date)
  const dKey = dayKey(d)
  const nowKey = dayKey(now)

  if (dKey === nowKey) {
    return todayTimeFmt.format(d)
  }

  const yesterday = new Date(now.getTime() - 86_400_000)
  if (dKey === dayKey(yesterday)) {
    return yesterdayLabel
  }

  return monthDayFmt.format(d)
}

/**
 * "2026년 5월 1일" — KST-anchored long date for editorial / hero surfaces.
 * Backend returns timezone-naive UTC; ``parseTimestamp`` normalizes that
 * before formatting so visitors in any TZ see the same date the author saw.
 */
export function formatLongDate(date: Date | string): string {
  return longDateFmt.format(parseTimestamp(date))
}

/** "2026. 5. 1." — KST-anchored short date for footer / meta strips. */
export function formatMediumDate(date: Date | string): string {
  return mediumDateFmt.format(parseTimestamp(date))
}
