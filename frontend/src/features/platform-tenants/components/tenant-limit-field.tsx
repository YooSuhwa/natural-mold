'use client'

import { useTranslations } from 'next-intl'

import { FormFieldShell } from '@/components/shared/form-field-shell'
import { Input } from '@/components/ui/input'

export type LimitKind = 'members' | 'spend' | 'runs'

/** Empty = no limit (`null`); otherwise a number ≥ 0 (0 blocks use, S04 검증). */
export function parseLimit(
  text: string,
  kind: LimitKind,
): { valid: boolean; value: number | null } {
  const trimmed = text.trim()
  if (trimmed === '') return { valid: true, value: null }
  const value = Number(trimmed)
  const whole = kind === 'spend' || Number.isInteger(value)
  return Number.isFinite(value) && value >= 0 && whole
    ? { valid: true, value }
    : { valid: false, value: null }
}

export function LimitField({
  id,
  kind,
  value,
  onChange,
}: {
  id: string
  kind: LimitKind
  value: string
  onChange: (value: string) => void
}) {
  const t = useTranslations('appSettings.platformTenants.limitField')
  const invalid = !parseLimit(value, kind).valid
  return (
    <FormFieldShell
      id={id}
      label={t(kind)}
      description={t('hint')}
      error={invalid ? t('invalid') : null}
    >
      <Input
        id={id}
        type="number"
        min={0}
        step={kind === 'spend' ? 'any' : 1}
        inputMode={kind === 'spend' ? 'decimal' : 'numeric'}
        value={value}
        aria-invalid={invalid || undefined}
        onChange={(event) => onChange(event.target.value)}
      />
    </FormFieldShell>
  )
}
