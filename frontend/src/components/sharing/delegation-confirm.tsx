'use client'

import { useEffect, useId, useRef } from 'react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import type { DelegatedAccount } from '@/lib/types/grant'

const KNOWN_TYPES = new Set(['credential', 'tool', 'mcp_server', 'agent', 'skill'])

interface DelegationConfirmProps {
  delegations: DelegatedAccount[]
  pending: boolean
  onConfirm: () => void
  onCancel: () => void
}

/** D17 `confirm_required`: list the account resources recipients will run with. */
export function DelegationConfirm({
  delegations,
  pending,
  onConfirm,
  onCancel,
}: DelegationConfirmProps) {
  const t = useTranslations('sharing.delegation')
  const titleId = useId()
  const descriptionId = useId()
  const cancelRef = useRef<HTMLButtonElement>(null)
  // Risky step: focus the safe choice first (13.12.3).
  useEffect(() => cancelRef.current?.focus(), [])

  return (
    <section
      role="group"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      className="space-y-2 rounded-md border border-status-warn/40 bg-status-warn/10 px-3 py-3 text-sm"
    >
      <p id={titleId} className="font-medium text-foreground">
        {t('title')}
      </p>
      <p id={descriptionId} className="text-xs text-muted-foreground">
        {t('description')}
      </p>
      {delegations.length > 0 ? (
        <ul className="list-disc space-y-0.5 pl-5 text-xs">
          {delegations.map((item) => (
            <li key={`${item.resource_type}:${item.resource_id}`}>
              {item.name}
              <span className="ml-1 text-muted-foreground">
                (
                {t(
                  KNOWN_TYPES.has(item.resource_type) ? `type.${item.resource_type}` : 'type.other',
                )}
                )
              </span>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex justify-end gap-2 pt-1">
        <Button ref={cancelRef} type="button" variant="outline" size="sm" onClick={onCancel}>
          {t('cancel')}
        </Button>
        <Button type="button" size="sm" disabled={pending} onClick={onConfirm}>
          {t('confirm')}
        </Button>
      </div>
    </section>
  )
}
