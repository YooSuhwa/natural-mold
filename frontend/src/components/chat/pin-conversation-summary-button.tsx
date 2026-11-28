'use client'

import { useContext, useRef } from 'react'
import { PinIcon, PinOffIcon } from 'lucide-react'
import { useAuiState } from '@assistant-ui/react'
import { AgUiChatActionContext } from '@/lib/ag-ui/chat-action-context'
import { useMoldyAgUiActivities } from '@/lib/ag-ui/activity-context'
import { findChatMessageLink } from '@/lib/ag-ui/chat-message-identity'
import { reportClientError } from '@/lib/logging/client-logger'
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
  const actions = useContext(AgUiChatActionContext)
  const activity = useMoldyAgUiActivities()
  const message = useAuiState((state) =>
    state.message?.status?.type === 'running' ? null : state.message,
  )
  const publicId =
    message && activity?.conversationId === conversationId
      ? findChatMessageLink(message, activity.links)?.uiMessageId
      : undefined
  const { summary, isMutating, pin, pinPublic, unpin } =
    usePinnedConversationSummary(conversationId)
  const isPinned = Boolean(
    summary &&
    (actions
      ? publicId && summary.source_public_message_id === publicId
      : summary.source_message_id === message?.id),
  )
  const label = isPinned ? unpinLabel : pinLabel
  const resolving = useRef(false)
  const disabled = conversationId === null || !message?.id || isMutating

  const toggle = async () => {
    if (!message || disabled || resolving.current) return
    if (isPinned) {
      unpin()
      return
    }
    if (!actions) {
      pin(message.id)
      return
    }
    if (!actions.resolveMessageId) return
    resolving.current = true
    try {
      const resolved = await actions.resolveMessageId(message)
      if (!resolved) return
      if (resolved === summary?.source_public_message_id) unpin()
      else pinPublic(resolved)
    } catch (error) {
      reportClientError('pinned-summary', 'Could not resolve the stored message.', error)
    } finally {
      resolving.current = false
    }
  }

  return (
    <button
      type="button"
      className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={() => void toggle()}
    >
      {isPinned ? (
        <PinOffIcon className="size-3" aria-hidden="true" />
      ) : (
        <PinIcon className="size-3" aria-hidden="true" />
      )}
    </button>
  )
}
