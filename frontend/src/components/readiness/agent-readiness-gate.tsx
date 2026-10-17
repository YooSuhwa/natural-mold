'use client'

import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useTranslations } from 'next-intl'
import { PlugZapIcon } from 'lucide-react'

import { StatusChip } from '@/components/shared/status-chip'
import { Button } from '@/components/ui/button'
import { onAgentNotReady, signalMatches } from '@/lib/chat/agent-not-ready-signal'
import { useAgentReadiness } from '@/lib/hooks/use-readiness'
import { readinessQueryKeys } from '@/lib/query-keys/readiness'

import { pendingConnections } from './readiness-actions'
import { ReadinessDialog } from './readiness-dialog'

/**
 * S21 on the chat screen (plan 13.5): a folded card while the agent is not ready, and
 * the panel opens by itself when a start/send answers `409 agent_not_ready`.
 */
export function AgentReadinessGate({
  agentId,
  agentName,
  conversationId,
}: {
  readonly agentId: string
  readonly agentName?: string | null
  /** Matches a legacy REST send refusal that only names the conversation. */
  readonly conversationId?: string | null
}) {
  const t = useTranslations('readiness.panel')
  const qc = useQueryClient()
  const { data } = useAgentReadiness(agentId)
  const [open, setOpen] = useState(false)

  useEffect(
    () =>
      onAgentNotReady((signal) => {
        if (!signalMatches(signal, agentId, conversationId)) return // e.g. the side chat
        void qc.invalidateQueries({ queryKey: readinessQueryKeys.detail(agentId) })
        setOpen(true)
      }),
    [agentId, conversationId, qc],
  )

  const notReady = data && data.overall !== 'ready'
  return (
    <>
      {notReady ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-border/60 px-4 py-2 text-sm">
          <PlugZapIcon className="size-4 text-muted-foreground" aria-hidden />
          <span className="min-w-0 flex-1">
            {t(`collapsed.${data.overall}`, { count: pendingConnections(data.items) })}
          </span>
          <StatusChip variant={data.overall} />
          <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
            {t('open')}
          </Button>
        </div>
      ) : null}
      <ReadinessDialog open={open} onOpenChange={setOpen} agentId={agentId} agentName={agentName} />
    </>
  )
}
