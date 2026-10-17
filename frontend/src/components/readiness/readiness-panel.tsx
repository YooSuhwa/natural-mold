'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { RequestAccessDialog } from '@/components/access-requests/request-access-dialog'
import { useNotifyOwner } from '@/lib/hooks/use-readiness'
import type { AgentReadiness } from '@/lib/types/readiness'

import { CredentialBindingDialog } from './credential-binding-dialog'
import {
  pendingConnections,
  readinessItemAction,
  type ReadinessItemAction,
} from './readiness-actions'
import { ReadinessItemRow } from './readiness-item-row'

type BindAction = Extract<ReadinessItemAction, { type: 'bind' }>
type RequestAction = Extract<ReadinessItemAction, { type: 'request' }>

interface ReadinessPanelProps {
  readonly agentId: string
  readonly agentName?: string | null
  readonly readiness: AgentReadiness
  /** After a per-user connection was made (the in-chat auth card answers `retry`). */
  readonly onConnected?: () => void
}

/** S21 items (plan 13.5): connect (S23), notify the owner, or request access (S18). */
export function ReadinessPanel({
  agentId,
  agentName,
  readiness,
  onConnected,
}: ReadinessPanelProps) {
  const t = useTranslations('readiness.panel')
  const notify = useNotifyOwner(agentId)
  const [binding, setBinding] = useState<BindAction | null>(null)
  const [request, setRequest] = useState<RequestAction | null>(null)

  const run = (action: ReadinessItemAction) => {
    if (action.type === 'bind') setBinding(action)
    else if (action.type === 'request') setRequest(action)
    else if (action.type === 'notify') {
      notify.mutate(undefined, {
        // 0 written: nobody could be notified (no owner credential/org); the button stays.
        onSuccess: ({ notified }) =>
          notified > 0 ? toast.success(t('notifySent')) : toast.error(t('notifyNone')),
        onError: () => toast.error(t('notifyFailed')),
      })
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground" aria-live="polite">
        {t(`summary.${readiness.overall}`, { count: pendingConnections(readiness.items) })}
      </p>
      <ul className="space-y-2">
        {readiness.items.map((item, index) => (
          <ReadinessItemRow
            key={`${item.kind}:${item.id ?? `count-${index}`}:${item.result}`}
            item={item}
            action={readinessItemAction(item, agentId)}
            notified={(notify.data?.notified ?? 0) > 0}
            notifying={notify.isPending}
            onAction={run}
          />
        ))}
      </ul>
      {binding ? (
        <CredentialBindingDialog
          open
          onOpenChange={(open) => !open && setBinding(null)}
          target={binding}
          agentName={agentName}
          onConnected={onConnected}
        />
      ) : null}
      {request ? (
        <RequestAccessDialog
          open
          onOpenChange={(open) => !open && setRequest(null)}
          resourceType={request.resourceType}
          resourceId={request.resourceId}
          resourceName={request.resourceName}
        />
      ) : null}
    </div>
  )
}
