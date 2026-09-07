'use client'

import { PinIcon, PinOffIcon } from 'lucide-react'
import { useAuiState } from '@assistant-ui/react'
import { usePinnedConversationSummary } from '@/lib/hooks/use-pinned-conversation-summary'

type PinConversationSummaryButtonProps = {
  readonly conversationId: string | null
  readonly pinLabel: string
  readonly unpinLabel: string
}

export function PinConversationSummaryButton({
  conversationId,
  pinLabel,
  unpinLabel,
}: PinConversationSummaryButtonProps) {
  const messageId = useAuiState((state) => state.message?.id)
  const isRunning = useAuiState((state) => state.message?.status?.type === 'running')
  const { summary, isMutating, pin, unpin } = usePinnedConversationSummary(conversationId)
  const isPinned = summary?.source_message_id === messageId
  const label = isPinned ? unpinLabel : pinLabel
  const disabled = conversationId === null || !messageId || isRunning || isMutating

  return (
    <button
      type="button"
      className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={() => {
        if (!messageId || disabled) return
        if (isPinned) {
          unpin()
        } else {
          pin(messageId)
        }
      }}
    >
      {isPinned ? (
        <PinOffIcon className="size-3" aria-hidden="true" />
      ) : (
        <PinIcon className="size-3" aria-hidden="true" />
      )}
    </button>
  )
}
