import type { ConversationListEnvelope, ConversationRunStatus } from '@/lib/types'

const ACTIVE_RUN_STATUSES: ReadonlySet<ConversationRunStatus> = new Set([
  'queued',
  'running',
  'canceling',
])

export function isActiveRunStatus(status: ConversationRunStatus | null | undefined): boolean {
  return status ? ACTIVE_RUN_STATUSES.has(status) : false
}

export function isInterruptedRunStatus(status: ConversationRunStatus | null | undefined): boolean {
  return status === 'interrupted'
}

export function conversationPagesContainActiveRun(
  pages: readonly ConversationListEnvelope[] | undefined,
): boolean {
  return Boolean(
    pages?.some((page) =>
      page.items.some((conversation) => isActiveRunStatus(conversation.active_run?.status)),
    ),
  )
}
