'use client'

import { useEffect, useId, useRef } from 'react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import type { DelegatedAccount } from '@/lib/types/grant'

const KNOWN_TYPES = new Set(['credential', 'tool', 'mcp_server', 'agent', 'skill'])

/** One line per lent account: the resource, its type, and whose account it is (D17). */
export function DelegationList({ delegations }: { delegations: DelegatedAccount[] }) {
  const t = useTranslations('sharing.delegation')
  if (delegations.length === 0) return null
  return (
    <ul className="list-disc space-y-0.5 pl-5 text-xs">
      {delegations.map((item) => (
        <li key={`${item.resource_type}:${item.resource_id}:${item.credential_id ?? ''}`}>
          {item.name}
          <span className="ml-1 text-muted-foreground">
            ({t(KNOWN_TYPES.has(item.resource_type) ? `type.${item.resource_type}` : 'type.other')})
          </span>
          {item.credential_name ? (
            <span className="block text-muted-foreground">
              {item.owner_name
                ? t('account', { credential: item.credential_name, owner: item.owner_name })
                : t('companyAccount', { credential: item.credential_name })}
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  )
}

interface DelegationConfirmProps {
  delegations: DelegatedAccount[]
  pending: boolean
  onConfirm: () => void
  onCancel: () => void
  /** Defaults to "확인하고 공유"; the agent settings save says "확인하고 저장". */
  confirmLabel?: string
}

/** D17 `confirm_required`: list the account resources recipients will run with. */
export function DelegationConfirm({
  delegations,
  pending,
  onConfirm,
  onCancel,
  confirmLabel,
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
      <DelegationList delegations={delegations} />
      <div className="flex justify-end gap-2 pt-1">
        <Button ref={cancelRef} type="button" variant="outline" size="sm" onClick={onCancel}>
          {t('cancel')}
        </Button>
        <Button type="button" size="sm" disabled={pending} onClick={onConfirm}>
          {confirmLabel ?? t('confirm')}
        </Button>
      </div>
    </section>
  )
}
