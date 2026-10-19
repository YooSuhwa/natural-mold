'use client'

import Link from 'next/link'
import { useTranslations } from 'next-intl'

import { StatusChip, type StatusChipVariant } from '@/components/shared/status-chip'
import { Button } from '@/components/ui/button'
import type { ReadinessItem } from '@/lib/types/readiness'

import type { ReadinessItemAction } from './readiness-actions'

function chipVariant(item: ReadinessItem): StatusChipVariant {
  switch (item.result) {
    case 'ready':
      return 'active'
    case 'connect_by_me':
    case 'owner_must_connect':
      return item.credential?.status === 'expired' ? 'expired' : 'auth_needed'
    case 'no_permission':
      return 'no_permission'
    default:
      return 'unreachable'
  }
}

function resultKey(item: ReadinessItem): string {
  if (item.result === 'connect_by_me' && item.credential?.status === 'expired') {
    return 'connect_by_me_expired'
  }
  if (item.result === 'no_permission' && item.required) return 'no_permission_required'
  return item.result
}

interface ReadinessItemRowProps {
  readonly item: ReadinessItem
  readonly action: ReadinessItemAction | null
  readonly notified: boolean
  readonly notifying: boolean
  readonly onAction: (action: ReadinessItemAction) => void
}

/** One S21 row: name (or a count), required/optional, the 12.1 result and its button. */
export function ReadinessItemRow({
  item,
  action,
  notified,
  notifying,
  onAction,
}: ReadinessItemRowProps) {
  const t = useTranslations('readiness.panel')
  const kind = t(`kind.${item.kind}`)
  const title = item.name ?? t('counted', { kind, count: item.count ?? 1 })

  return (
    <li className="flex flex-wrap items-center gap-3 rounded-md border border-border px-3 py-2">
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium">{title}</p>
        <p className="text-xs text-muted-foreground">
          {t(`result.${resultKey(item)}`)} · {item.required ? t('required') : t('optional')}
        </p>
      </div>
      <StatusChip variant={chipVariant(item)} label={t(`chip.${item.result}`)} />
      {action?.type === 'credentials' ? (
        <Button size="sm" variant="outline" render={<Link href="/settings/credentials" />}>
          {t('openCredentials')}
        </Button>
      ) : action ? (
        <Button
          size="sm"
          variant={action.type === 'bind' ? 'default' : 'outline'}
          disabled={action.type === 'notify' && (notified || notifying)}
          onClick={() => onAction(action)}
        >
          {action.type === 'bind'
            ? t('connect')
            : action.type === 'notify'
              ? notified
                ? t('notified')
                : t('notifyOwner')
              : t('requestAccess')}
        </Button>
      ) : null}
    </li>
  )
}
