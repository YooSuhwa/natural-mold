'use client'

import { useTranslations } from 'next-intl'

import type { RegistrationState } from '@/lib/types/approval'
import { cn } from '@/lib/utils'

/** Marks a tool or MCP server the administrators have not approved (plan 3.7). */
export function RegistrationBadge({ state }: { state: RegistrationState | undefined }) {
  const t = useTranslations('registration.state')
  if (!state || state === 'approved') return null
  return (
    <span
      data-registration={state}
      className={cn(
        'rounded-full px-1.5 py-0.5 moldy-ui-micro font-medium',
        state === 'pending'
          ? 'moldy-status-warn moldy-status-soft'
          : 'bg-muted text-muted-foreground',
      )}
    >
      {t(state)}
    </span>
  )
}
