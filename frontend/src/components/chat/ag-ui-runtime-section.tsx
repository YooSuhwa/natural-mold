'use client'

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import type { AttachmentAdapter, DictationAdapter, FeedbackAdapter } from '@assistant-ui/react'
import { useAgUiRuntime } from '@assistant-ui/react-ag-ui'
import { useTranslations } from 'next-intl'
import { AgUiActivityProvider } from '@/lib/ag-ui/activity-context'
import { MOLDY_AG_UI_DATA_UI } from '@/lib/ag-ui/activity-data-ui'
import { useMoldyAgUiActivityEffects } from '@/lib/ag-ui/activity-effects'
import { mergeAgUiRunActivities } from '@/lib/ag-ui/activity-native-summary'
import { toMoldyRunActivities } from '@/lib/ag-ui/activity-projection'
import { AgUiFollowupCompletionContext } from '@/lib/ag-ui/followup-completion-context'
import { AgUiChatActionContext } from '@/lib/ag-ui/chat-action-context'
import { agUiAttachmentAdapter, agUiFeedbackAdapter } from '@/lib/ag-ui/chat-adapters'
import { useAgUiChatActions } from '@/lib/ag-ui/chat-actions'
import { useAgUiHitlActions } from '@/lib/ag-ui/chat-actions-interrupts'
import { useChatSession } from '@/lib/ag-ui/use-chat-session'
import { useDurableAgUiHistory } from '@/lib/ag-ui/use-durable-ag-ui-history'
import { ALL_DATA_UI } from '@/lib/chat/data-ui'
import { blocksInputRetry } from '@/lib/chat-runs/recovery-error'
import { terminalRunNotice } from '@/lib/chat/langgraph-runtime/stream-thread-state-projection'
import { AgUiSubagentRuntimeProvider } from '@/lib/chat/langgraph-runtime/subagent-runtime'
import type { ConversationRuntimeStatus } from '@/lib/stores/chat-navigator-store'
import type { ConversationRun, Message } from '@/lib/types'
import { AgUiChildPendingForms } from './ag-ui-child-pending-forms'
import { RuntimeFrame, type ThreadRenderProps } from './chat-runtime-frame'

const DATA_UI = [...ALL_DATA_UI, ...MOLDY_AG_UI_DATA_UI] as const

interface AgUiRuntimeSectionProps {
  readonly agentId: string
  readonly attachmentAdapter?: AttachmentAdapter
  readonly conversationId: string
  readonly dictationAdapter?: DictationAdapter
  readonly feedbackAdapter?: FeedbackAdapter
  readonly onBeforeNewMessage?: () => void
  readonly onNewMessageAccepted?: () => void
  readonly onRuntimeStatusChange: (status: ConversationRuntimeStatus) => void
  readonly onStreamEnd: (didMutate: boolean) => void
  readonly serverActiveRunId?: string | null
  readonly serverLatestRun: ConversationRun | null
  readonly serverMessages: readonly Message[]
  readonly threadProps: ThreadRenderProps
}

export function AgUiRuntimeSection(props: AgUiRuntimeSectionProps) {
  const { conversationId, agentId, serverMessages, threadProps } = props
  const t = useTranslations('chat.page')
  const { agent, session } = useChatSession(props)
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot)
  const adapters = useMemo(
    () => ({
      history: session.history,
      attachments: props.attachmentAdapter && agUiAttachmentAdapter(props.attachmentAdapter),
      feedback:
        props.feedbackAdapter &&
        agUiFeedbackAdapter(props.feedbackAdapter, () => agent.readSnapshot()),
      dictation: props.dictationAdapter,
    }),
    [session, agent, props.attachmentAdapter, props.feedbackAdapter, props.dictationAdapter],
  )
  const runtime = useAgUiRuntime({
    agent,
    adapters,
    resumeTranscript: 'appended',
    showThinking: false,
    autoCancelPendingToolCalls: false,
    unstable_enableMessageQueue: false,
    isSendDisabled: threadProps.readOnlyConversation || state.readDenied || !state.snapshot,
  })

  const [claimedRunId, setClaimedRunId] = useState<string | null>(null)
  const { onStreamEnd, onRuntimeStatusChange } = props
  const onSettled = useCallback(() => onStreamEnd(false), [onStreamEnd])
  const isRunning = useDurableAgUiHistory({
    runtime,
    session,
    conversationId,
    agent,
    readOnly: threadProps.readOnlyConversation,
    onSettled,
    serverActiveRunId: props.serverActiveRunId ?? claimedRunId,
  })
  useEffect(() => {
    onRuntimeStatusChange(isRunning ? 'running' : 'idle')
  }, [isRunning, onRuntimeStatusChange])

  const { actions, messageQueue, retryFailedInput } = useAgUiChatActions({
    agent,
    conversationId,
    attachmentAdapter: adapters.attachments,
    activeRunId: state.activeRunId,
    activePhysicalRunId: state.physicalRunId,
    runtimeIsRunning: isRunning,
    onClaimedRun: setClaimedRunId,
  })
  const hitl = useAgUiHitlActions({ runtime, conversationId, agentId })
  const activityRunId = state.physicalRunId ?? state.lastTerminalPhysicalRunId
  const activities = useMemo(
    () =>
      activityRunId
        ? mergeAgUiRunActivities(
            state.nativeActivities,
            toMoldyRunActivities(state.activities, activityRunId),
            activityRunId,
          )
        : [],
    [state.nativeActivities, state.activities, activityRunId],
  )
  useMoldyAgUiActivityEffects(conversationId, state.activities, {
    runId: activityRunId,
    liveActivityIds: state.liveActivityIds,
    serverMessages: state.snapshot && !state.readDenied ? serverMessages : undefined,
  })

  if (state.readDenied) {
    return (
      <p role="alert" className="p-4 text-sm text-muted-foreground">
        {t('historyUnavailable')}
      </p>
    )
  }

  const fallbackNotice = props.serverLatestRun && terminalRunNotice(props.serverLatestRun)
  const currentFailure = state.failedPhysicalRunId ?? undefined
  const matchingNotice = fallbackNotice?.id === currentFailure ? fallbackNotice : null
  return (
    <RuntimeFrame
      runtime={runtime}
      threadProps={{
        ...threadProps,
        threadHeader: (
          <>
            {threadProps.threadHeader}
            {!threadProps.readOnlyConversation && (
              <AgUiChildPendingForms conversationId={conversationId} snapshot={state} />
            )}
          </>
        ),
      }}
      hitlValue={hitl}
      resetContextOnQueueAcceptance={false}
      deepAgentsState={state.deepAgentsState}
      activities={activities}
      dataUI={DATA_UI}
      failedRunId={
        blocksInputRetry(state.recoveryError ?? matchingNotice?.recoveryError)
          ? undefined
          : currentFailure
      }
      partialOutput={
        state.partialOutput === undefined ? matchingNotice?.partialOutput : state.partialOutput
      }
      messageQueue={threadProps.readOnlyConversation ? undefined : messageQueue}
      retryFailedInput={retryFailedInput}
      wrapThread={(children) => (
        <AgUiSubagentRuntimeProvider
          conversationId={conversationId}
          activities={state.activities}
          links={state.messageLinks}
        >
          <AgUiActivityProvider
            physicalRunId={state.physicalRunId}
            conversationId={conversationId}
            activities={state.activities}
            links={state.messageLinks}
          >
            <AgUiFollowupCompletionContext.Provider value={state.followupRunId}>
              <AgUiChatActionContext.Provider value={actions}>
                {children}
              </AgUiChatActionContext.Provider>
            </AgUiFollowupCompletionContext.Provider>
          </AgUiActivityProvider>
        </AgUiSubagentRuntimeProvider>
      )}
    />
  )
}
