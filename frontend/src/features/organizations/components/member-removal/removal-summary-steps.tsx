'use client'

import { useId } from 'react'
import { useTranslations } from 'next-intl'

import { Textarea } from '@/components/ui/textarea'
import type { MemberExitPreview } from '@/lib/types/member-exit'

/** Step 2 (S15): what the removal revokes and stops — read-only, from the preview. */
export function RemovalEffectsStep({ preview }: { preview: MemberExitPreview }) {
  const t = useTranslations('orgAdmin.memberRemoval.effects')
  const items = [
    { key: 'grants', count: preview.grants.length },
    { key: 'groups', count: preview.group_ids.length },
    { key: 'roles', count: preview.roles.length },
    { key: 'triggers', count: preview.trigger_ids.length },
    { key: 'deployments', count: preview.deployment_ids.length },
    { key: 'apiKeys', count: preview.api_key_ids.length },
  ] as const
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">{t('description')}</p>
      <ul className="space-y-1.5 text-sm">
        {items.map((item) => (
          <li key={item.key} className="flex items-center justify-between gap-3">
            <span>{t(item.key)}</span>
            <span className="text-muted-foreground">{t('count', { count: item.count })}</span>
          </li>
        ))}
      </ul>
      <p className="text-xs text-muted-foreground">{t('delegation')}</p>
    </div>
  )
}

interface ConfirmStepProps {
  name: string
  transfers: number
  deletes: number
  reason: string
  onReason: (value: string) => void
}

/** Step 3 (S15): governance reason (audited) and the final summary. */
export function RemovalConfirmStep({
  name,
  transfers,
  deletes,
  reason,
  onReason,
}: ConfirmStepProps) {
  const t = useTranslations('orgAdmin.memberRemoval.confirm')
  const reasonId = useId()
  return (
    <div className="space-y-3">
      <ul className="list-disc space-y-1 pl-5 text-sm">
        <li>{t('transfers', { count: transfers })}</li>
        <li>{t('deletes', { count: deletes })}</li>
        <li>{t('account', { name })}</li>
        <li>{t('retention')}</li>
      </ul>
      <div className="space-y-1.5">
        <label htmlFor={reasonId} className="text-sm font-medium">
          {t('reason')}
        </label>
        <Textarea
          id={reasonId}
          value={reason}
          maxLength={500}
          aria-required
          onChange={(event) => onReason(event.target.value)}
        />
      </div>
    </div>
  )
}
