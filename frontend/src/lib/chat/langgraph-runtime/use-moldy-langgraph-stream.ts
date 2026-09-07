'use client'

import { useCallback, useMemo } from 'react'
import {
  useExternalStoreRuntime,
  type AttachmentAdapter,
  type DictationAdapter,
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
import type { ConversationRun, Message as MoldyMessage } from '@/lib/types'
import { useServerMessageQueue } from '@/lib/chat/message-queue/use-server-message-queue'
import { shouldRouteComposerToServerQueue } from '@/lib/chat/message-queue/server-message-queue'
import type { ServerMessageQueueOptions } from '@/lib/chat/message-queue/server-message-queue-contract'
import type { ConversationRunInput } from '@/lib/api/conversation-run-inputs'

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
  dictationAdapter?: DictationAdapter
  onBeforeSubmit?: () => void
  onRunStartAccepted?: () => void
  serverLatestRun?: ConversationRun | null
  serverRunIsActive?: boolean
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
  dictationAdapter,
  onBeforeSubmit,
  onRunStartAccepted,
  serverLatestRun = null,
  serverRunIsActive = false,
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
  const submitCheckpoint = useSubmitCheckpointController(conversationId)
  const handleRunStartAccepted = useCallback(
    (runId?: string): void => {
      if (runId) submitCheckpoint.acceptPendingSubmit(runId)
      onRunStartAccepted?.()
    },
    [onRunStartAccepted, submitCheckpoint],
  )
  const reconciliation = useStreamReconciliationController({
    agentId,
    conversationId,
    onRunStartAccepted: handleRunStartAccepted,
    clearBranchPickerSuppression,
  })
  const {
    stream,
    threadRunNotice,
    serverMessageMetadata,
    serverMessages: hydratedServerMessages,
    serverInterrupts,
    claimedQueueRunInFlight,
    pendingEditRender,
    pendingReloadRender,
    postRunHydrationPending,
  } = reconciliation
  const { pendingSubmit: cachedPendingNewSubmit, clearPendingSubmit } = submitCheckpoint
  const durableThreadRunNotice = useMemo(() => {
    const pendingSubmitMatchesLatestRun =
      cachedPendingNewSubmit !== null &&
      serverLatestRun !== null &&
      (cachedPendingNewSubmit.acceptedRunId === serverLatestRun.id ||
        cachedPendingNewSubmit.content === serverLatestRun.input_preview)
    if (
      threadRunNotice?.status === 'canceling' &&
      serverLatestRun &&
      serverLatestRun.id === threadRunNotice.id &&
      (serverLatestRun.status === 'canceled' ||
        serverLatestRun.status === 'stale' ||
        serverLatestRun.status === 'failed')
    ) {
      return {
        id: serverLatestRun.id,
        status: serverLatestRun.status,
        ...(serverLatestRun.status === 'failed' && serverLatestRun.error_message
          ? { errorMessage: serverLatestRun.error_message }
          : {}),
      }
    }
    if (threadRunNotice) return threadRunNotice
    if (
      stream.isLoading ||
      claimedQueueRunInFlight ||
      (cachedPendingNewSubmit && !pendingSubmitMatchesLatestRun) ||
      serverRunIsActive ||
      !serverLatestRun
    ) {
      return null
    }
    if (
      serverLatestRun.status !== 'canceled' &&
      serverLatestRun.status !== 'canceling' &&
      serverLatestRun.status !== 'stale' &&
      serverLatestRun.status !== 'failed'
    ) {
      return null
    }
    return {
      id: serverLatestRun.id,
      status: serverLatestRun.status,
      ...(serverLatestRun.status === 'failed' && serverLatestRun.error_message
        ? { errorMessage: serverLatestRun.error_message }
        : {}),
    }
  }, [
    cachedPendingNewSubmit,
    claimedQueueRunInFlight,
    serverLatestRun,
    serverRunIsActive,
    stream.isLoading,
    threadRunNotice,
  ])
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
    durableThreadRunNotice?.status === 'stale'
      ? tReconnect('stale')
      : durableThreadRunNotice?.status === 'failed'
        ? (durableThreadRunNotice.errorMessage ?? tPage('runFailed'))
        : durableThreadRunNotice?.status === 'canceling'
          ? tPage('canceling')
          : tPage('canceled')
  const runtimeMessages = useStreamRuntimeMessages({
    conversationId,
    stream,
    messagesWithInterrupts: interruptView.messagesWithInterrupts,
    interruptCount: interruptView.payloads.length,
    claimedQueueRunInFlight,
    threadRunNotice: durableThreadRunNotice,
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
    if (!feedbackAdapter && !attachmentAdapter && !dictationAdapter) return undefined
    return {
      ...(feedbackAdapter ? { feedback: feedbackAdapter } : {}),
      ...(attachmentAdapter ? { attachments: attachmentAdapter } : {}),
      ...(dictationAdapter ? { dictation: dictationAdapter } : {}),
    }
  }, [feedbackAdapter, attachmentAdapter, dictationAdapter])
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
  const submitQueuedTransport = reconciliation.submitQueuedInput
  const handleClaimedQueueRun = reconciliation.handleClaimedQueueRun
  const submitQueuedInput = useCallback<ServerMessageQueueOptions['submit']>(
    (message, options) => submitQueuedTransport(message, options.strategy, options.requestId),
    [submitQueuedTransport],
  )
  const { controller: messageQueue, snapshot: messageQueueSnapshot } = useServerMessageQueue({
    conversationId,
    submit: submitQueuedInput,
    onClaimedRun: handleClaimedQueueRun,
  })
  const onCancelWithQueueRefresh = useCallback(async () => {
    await onCancel()
    await messageQueue.refresh()
  }, [messageQueue, onCancel])
  const retryFailedInput = useCallback(
    async (input: ConversationRunInput) => {
      const requestId = crypto.randomUUID()
      try {
        const accepted = await reconciliation.retryFailedInput(input, requestId)
        if (accepted.runId) reconciliation.handleClaimedQueueRun(accepted.runId)
      } catch (error) {
        const reconciled = await messageQueue.reconcileRequest(requestId)
        if (!reconciled) throw error
        if (reconciled.run_id) reconciliation.handleClaimedQueueRun(reconciled.run_id)
        return
      }
      await messageQueue.refresh()
    },
    [messageQueue, reconciliation],
  )

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
    onCancel: onCancelWithQueueRefresh,
    queue: shouldRouteComposerToServerQueue(
      messageQueueSnapshot,
      runtimeIsRunning || serverRunIsActive,
    )
      ? messageQueue.adapter
      : undefined,
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
    messageQueue,
    retryFailedInput,
    threadRunNotice: durableThreadRunNotice,
  }
}
