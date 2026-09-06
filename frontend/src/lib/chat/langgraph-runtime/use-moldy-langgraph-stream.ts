'use client'

import { useCallback, useMemo } from 'react'
import {
  useExternalStoreRuntime,
  type AttachmentAdapter,
  type FeedbackAdapter,
} from '@assistant-ui/react'
import { HumanMessage } from '@langchain/core/messages'
import { useChannel, type Channel } from '@langchain/react'
import { useSetAtom } from 'jotai'
import { useTranslations } from 'next-intl'
import { reduceProtocolActivity } from './activity-protocol'
import type { RunActivity } from './activity-model'
import { selectDeepAgentsState } from './deepagents-state'
import { refreshThreadLifecycleStream } from './lifecycle-subscription'
import { useHitlDecisionController } from './use-hitl-decision-controller'
import { useStreamCommandController } from './use-stream-command-controller'
import { useStreamInterruptView } from './use-stream-interrupt-view'
import { useStreamReconciliationController } from './use-stream-reconciliation-controller'
import { useStreamRuntimeMessages } from './use-stream-runtime-messages'
import { useStreamVisibleMessages } from './use-stream-visible-messages'
import { useSubmitCheckpointController } from './use-submit-checkpoint-controller'
import {
  chatCancelInFlightAtom,
  pendingEditBranchPickerSuppressionAtom,
} from '@/lib/stores/chat-store'
import type { Message as MoldyMessage } from '@/lib/types'

export { messagesFromServerMessages } from './stream-thread-state-projection'
export {
  appendPendingNewSubmitMessage,
  primeStickyConversationMessagesFromThreadState,
} from './stream-message-projection'
interface UseMoldyLangGraphStreamOptions {
  agentId: string
  conversationId: string
  feedbackAdapter?: FeedbackAdapter
  attachmentAdapter?: AttachmentAdapter
  onBeforeSubmit?: () => void
  onRunStartAccepted?: () => void
  serverMessages?: readonly MoldyMessage[]
}

const ACTIVITY_CHANNELS = [
  'messages',
  'tools',
  'values',
  'updates',
  'lifecycle',
  'tasks',
  'checkpoints',
  'custom',
] as const satisfies readonly Channel[]

export function useMoldyLangGraphStream({
  agentId,
  conversationId,
  feedbackAdapter,
  attachmentAdapter,
  onBeforeSubmit,
  onRunStartAccepted,
  serverMessages,
}: UseMoldyLangGraphStreamOptions) {
  const setChatCancelInFlight = useSetAtom(chatCancelInFlightAtom)
  const setPendingBranchPickerSuppression = useSetAtom(pendingEditBranchPickerSuppressionAtom)
  const tPage = useTranslations('chat.page')
  const tReconnect = useTranslations('chat.reconnect')
  const clearBranchPickerSuppression = useCallback(
    () => setPendingBranchPickerSuppression(null),
    [setPendingBranchPickerSuppression],
  )
  const reconciliation = useStreamReconciliationController({
    agentId,
    conversationId,
    onRunStartAccepted,
    clearBranchPickerSuppression,
  })
  const {
    stream,
    threadRunNotice,
    serverMessageMetadata,
    serverMessages: hydratedServerMessages,
    serverInterrupts,
    pendingEditRender,
    pendingReloadRender,
    postRunHydrationPending,
  } = reconciliation
  const submitCheckpoint = useSubmitCheckpointController(conversationId)
  const { pendingSubmit: cachedPendingNewSubmit, clearPendingSubmit } = submitCheckpoint
  const activityEvents = useChannel(stream, ACTIVITY_CHANNELS, undefined, { bufferSize: 300 })
  const activities = useMemo(
    () =>
      activityEvents.reduce<RunActivity[]>(
        (current, event) => reduceProtocolActivity(current, event),
        [],
      ),
    [activityEvents],
  )
  const deepAgentsState = useMemo(() => selectDeepAgentsState(stream.values ?? {}), [stream.values])
  const visible = useStreamVisibleMessages({
    streamMessages: hydratedServerMessages ?? stream.messages,
    isLoading: stream.isLoading,
    postRunHydrationPending,
    serverMessages,
    serverMessageMetadata,
    pendingSubmit: cachedPendingNewSubmit,
    clearPendingSubmit,
    pendingEdit: pendingEditRender,
    pendingReload: pendingReloadRender,
  })
  const interruptView = useStreamInterruptView({
    conversationId,
    stream,
    serverInterrupts,
    messages: visible.messages,
  })
  const terminalNoticeText =
    threadRunNotice?.status === 'stale'
      ? tReconnect('stale')
      : threadRunNotice?.status === 'failed'
        ? (threadRunNotice.errorMessage ?? tPage('runFailed'))
        : tPage('canceled')
  const runtimeMessages = useStreamRuntimeMessages({
    conversationId,
    stream,
    messagesWithInterrupts: interruptView.messagesWithInterrupts,
    interruptCount: interruptView.payloads.length,
    threadRunNotice,
    terminalNoticeText,
    hydratedMessagesPresent: hydratedServerMessages !== null,
    pendingEdit: pendingEditRender,
    pendingReload: pendingReloadRender,
    settling: visible.settling,
    recordLatestVisibleMessages: reconciliation.recordLatestVisibleMessages,
  })
  const {
    messages,
    checkpointVisibleMessages,
    langChainMessages: stickyMessagesWithTerminalNotice,
    runtimeIsRunning,
  } = runtimeMessages
  const adapters = useMemo(() => {
    if (!feedbackAdapter && !attachmentAdapter) return undefined
    return {
      ...(feedbackAdapter ? { feedback: feedbackAdapter } : {}),
      ...(attachmentAdapter ? { attachments: attachmentAdapter } : {}),
    }
  }, [feedbackAdapter, attachmentAdapter])
  const { onNew, onEdit, onReload, onCancel } = useStreamCommandController({
    conversationId,
    stream,
    visibleMessages: checkpointVisibleMessages,
    langChainMessages: stickyMessagesWithTerminalNotice,
    convertedMessages: messages,
    onBeforeSubmit,
    setChatCancelInFlight,
    clearBranchPickerSuppression,
    submitCheckpoint,
    reconciliation: reconciliation.commandActions,
  })

  const refreshLifecycle = useCallback(() => refreshThreadLifecycleStream(stream), [stream])
  const { onResumeDecisions, registerDecision } = useHitlDecisionController({
    conversationId,
    stream,
    interruptPayloads: interruptView.payloads,
    interruptPayloadsById: interruptView.payloadsById,
    allInterruptPayloadsById: interruptView.allPayloadsById,
    refreshLifecycle,
    updateResolvedInterrupts: interruptView.updateResolved,
  })

  const assistantRuntime = useExternalStoreRuntime({
    messages,
    isRunning: runtimeIsRunning,
    adapters,
    onNew,
    onEdit,
    onReload,
    onCancel,
  })

  const sendMessage = useCallback(
    async (content: string) => {
      const trimmed = content.trim()
      if (!trimmed) return
      reconciliation.commandActions.setThreadRunNotice(null)
      reconciliation.commandActions.clearServerHydrationState()
      await stream.submit({ messages: [new HumanMessage(trimmed)] })
    },
    [reconciliation.commandActions, stream],
  )
  return {
    stream,
    assistantRuntime,
    activities,
    deepAgentsState,
    sendMessage,
    onResumeDecisions,
    registerDecision,
  }
}
