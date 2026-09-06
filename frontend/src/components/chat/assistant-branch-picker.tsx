'use client'

import { useCallback, useMemo, useState } from 'react'
import { useAuiState } from '@assistant-ui/react'
import { ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useAtomValue } from 'jotai'
import { useConversationBranchSwitch } from '@/lib/hooks/use-conversation-branch-switch'
import { useChatConversationId } from '@/components/chat/conversation-context'
import { getMessageCopyText } from '@/components/chat/message-copy'
import { reportClientError, reportClientWarning } from '@/lib/logging/client-logger'
import { pendingEditBranchPickerSuppressionAtom } from '@/lib/stores/chat-store'

interface BranchMeta {
  readonly branches?: readonly string[]
  readonly siblingCheckpointIds?: readonly string[]
  readonly activeBranchId?: string
  readonly branchCheckpointId?: string | null
  readonly branchIndex?: number | null
  readonly branchTotal?: number | null
  readonly moldyBranchPickerDisplayOnly?: boolean
  readonly moldySuppressBranchPicker?: boolean
}

function canRenderBranchPicker(
  conversationId: string | null,
  meta: BranchMeta,
): meta is BranchMeta & { readonly branchIndex: number; readonly branchTotal: number } {
  const siblingCheckpoints = meta.siblingCheckpointIds ?? []
  const { branchIndex, branchTotal } = meta
  const hasBranchNumbers =
    typeof branchIndex === 'number' && typeof branchTotal === 'number' && branchTotal >= 2
  return (
    !!conversationId &&
    meta.moldySuppressBranchPicker !== true &&
    hasBranchNumbers &&
    (meta.moldyBranchPickerDisplayOnly === true || siblingCheckpoints.length === branchTotal)
  )
}

/** Keeps branch selection side effects ordered: switch, active-query refetch, then runtime event. */
export function BranchPicker() {
  const t = useTranslations('chat.branch')
  const conversationId = useChatConversationId()
  const switchConversationBranch = useConversationBranchSwitch(conversationId)
  const [pendingCheckpointId, setPendingCheckpointId] = useState<string | null>(null)
  const threadIsRunning = useAuiState((s) => s.thread.isRunning)
  const pendingEditSuppression = useAtomValue(pendingEditBranchPickerSuppressionAtom)
  const messageId = useAuiState((s) => (typeof s.message?.id === 'string' ? s.message.id : null))
  const messageText = useAuiState((s) => getMessageCopyText(s.message?.content))
  const meta = useAuiState(
    (s) =>
      ((s.message?.metadata as { custom?: BranchMeta } | undefined)?.custom ?? {}) as BranchMeta,
  )
  const siblingCheckpoints = useMemo(
    () => meta.siblingCheckpointIds ?? [],
    [meta.siblingCheckpointIds],
  )
  const displayOnly = meta.moldyBranchPickerDisplayOnly === true

  const switchTo = useCallback(
    async (targetIdx: number) => {
      if (!conversationId || pendingCheckpointId || threadIsRunning || displayOnly) return
      const checkpointId = siblingCheckpoints[targetIdx]
      if (!checkpointId) {
        reportClientWarning('BranchPicker', 'missing checkpoint id for sibling idx', targetIdx)
        return
      }
      setPendingCheckpointId(checkpointId)
      try {
        await switchConversationBranch(checkpointId)
      } catch (error) {
        reportClientError('BranchPicker', 'switch failed', error)
      } finally {
        setPendingCheckpointId(null)
      }
    },
    [
      conversationId,
      displayOnly,
      pendingCheckpointId,
      siblingCheckpoints,
      switchConversationBranch,
      threadIsRunning,
    ],
  )

  const pendingEditMatchesMessage =
    pendingEditSuppression?.conversationId === conversationId &&
    (messageId != null
      ? pendingEditSuppression.messageId === messageId
      : pendingEditSuppression.content === messageText)
  const branchIsNewest =
    typeof meta.branchTotal !== 'number' ||
    meta.branchTotal < 2 ||
    meta.branchIndex === meta.branchTotal - 1
  if (pendingEditMatchesMessage && !branchIsNewest) return null
  if (!canRenderBranchPicker(conversationId, meta)) return null

  const currentIdx = meta.branchIndex
  const total = meta.branchTotal
  const isSwitching = pendingCheckpointId !== null
  const controlsDisabled = isSwitching || threadIsRunning || displayOnly
  return (
    <span
      className="inline-flex items-center gap-0.5 moldy-ui-micro tabular-nums text-muted-foreground"
      data-moldy-branch-picker="true"
    >
      <button
        type="button"
        className="inline-flex size-4 items-center justify-center rounded hover:bg-accent disabled:opacity-30"
        disabled={controlsDisabled || currentIdx <= 0}
        onClick={() => void switchTo(currentIdx - 1)}
        aria-label={t('previous')}
      >
        <ChevronLeftIcon className="size-3" />
      </button>
      <span className="px-1">
        {currentIdx + 1}/{total}
      </span>
      <button
        type="button"
        className="inline-flex size-4 items-center justify-center rounded hover:bg-accent disabled:opacity-30"
        disabled={controlsDisabled || currentIdx >= total - 1}
        onClick={() => void switchTo(currentIdx + 1)}
        aria-label={t('next')}
      >
        <ChevronRightIcon className="size-3" />
      </button>
    </span>
  )
}
