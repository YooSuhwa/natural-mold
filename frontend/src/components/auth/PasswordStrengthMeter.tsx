'use client'

import { useTranslations } from 'next-intl'

import { cn } from '@/lib/utils'

interface Props {
  password: string
}

interface Score {
  /** 0..4 — number of segments to highlight. */
  segments: number
  /** Translation key under `auth.register.strength.*`, or 'tooShort'. */
  labelKey: 'tooShort' | 'weak' | 'medium' | 'strong' | 'veryStrong'
  /** Tailwind class for active segments + label color. */
  tone: 'destructive' | 'warn' | 'success'
}

function score(password: string): Score {
  const length = password.length
  if (length === 0)
    return { segments: 0, labelKey: 'tooShort', tone: 'destructive' }
  if (length < 8)
    return { segments: 0, labelKey: 'tooShort', tone: 'destructive' }
  if (length < 10)
    return { segments: 1, labelKey: 'weak', tone: 'warn' }
  if (length < 14)
    return { segments: 2, labelKey: 'medium', tone: 'warn' }
  if (length < 18)
    return { segments: 3, labelKey: 'strong', tone: 'success' }
  // very strong: 18+ or mixed alphanum + symbol
  const hasLetter = /[a-zA-Z]/.test(password)
  const hasDigit = /\d/.test(password)
  const hasSymbol = /[^a-zA-Z0-9]/.test(password)
  const mixedBonus = hasLetter && hasDigit && hasSymbol
  if (mixedBonus || length >= 18)
    return { segments: 4, labelKey: 'veryStrong', tone: 'success' }
  return { segments: 3, labelKey: 'strong', tone: 'success' }
}

const TONE_BG: Record<Score['tone'], string> = {
  destructive: 'bg-destructive',
  warn: 'bg-status-warn',
  success: 'bg-status-success',
}

const TONE_TEXT: Record<Score['tone'], string> = {
  destructive: 'text-destructive',
  warn: 'text-status-warn',
  success: 'text-status-success',
}

export function PasswordStrengthMeter({ password }: Props) {
  const t = useTranslations('auth.register.strength')
  const result = score(password)

  return (
    <div className="mt-2">
      <div
        role="meter"
        aria-label={t('label')}
        aria-valuenow={result.segments}
        aria-valuemin={0}
        aria-valuemax={4}
        className="flex h-1 gap-1"
      >
        {[0, 1, 2, 3].map((i) => (
          <span
            key={i}
            className={cn(
              'flex-1 rounded-full transition-colors',
              i < result.segments ? TONE_BG[result.tone] : 'bg-muted',
            )}
          />
        ))}
      </div>
      <p className={cn('mt-1 text-xs', TONE_TEXT[result.tone])}>{t(result.labelKey)}</p>
    </div>
  )
}
