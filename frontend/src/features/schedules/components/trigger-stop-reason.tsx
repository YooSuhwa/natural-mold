'use client'

import { useTranslations } from 'next-intl'
import { CircleSlashIcon } from 'lucide-react'

/** F-08 BLOCKED reasons (`app/authz/dispatch_block.py` `BlockReason`). */
export const TRIGGER_STOP_REASONS = [
  'permission_revoked',
  'owner_left',
  'org_archived',
  'tenant_suspended',
  'agent_archived',
  'delegation_broken',
  'required_unavailable',
  'auth_required',
  'authz_unavailable',
] as const

export type TriggerStopReasonCode = (typeof TRIGGER_STOP_REASONS)[number]

const KNOWN: ReadonlySet<string> = new Set(TRIGGER_STOP_REASONS)

/**
 * A run's `reason_code`, or the trigger's `last_error` written as `<reason>[: <code>]`
 * by the BLOCKED path; anything else (free-text run errors) is not a stop reason.
 */
export function stopReasonOf(
  reasonCode: string | null | undefined,
  lastError?: string | null,
): TriggerStopReasonCode | null {
  const candidate = reasonCode ?? lastError?.split(':', 1)[0]?.trim() ?? null
  return candidate && KNOWN.has(candidate) ? (candidate as TriggerStopReasonCode) : null
}

/** "정지 사유" line (plan 13.9 row 3); renders nothing without a known reason. */
export function TriggerStopReason({
  reasonCode,
  lastError,
}: {
  reasonCode?: string | null
  lastError?: string | null
}) {
  const t = useTranslations('agent.triggerStopReason')
  const reason = stopReasonOf(reasonCode, lastError)
  if (!reason) return null
  return (
    <p className="flex items-center gap-1 text-xs text-status-danger">
      <CircleSlashIcon className="size-3" aria-hidden="true" />
      {t('label', { reason: t(`reasons.${reason}`) })}
    </p>
  )
}
