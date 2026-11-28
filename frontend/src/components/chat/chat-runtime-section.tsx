'use client'

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import dynamic from 'next/dynamic'
import type { AttachmentAdapter, DictationAdapter, FeedbackAdapter } from '@assistant-ui/react'
import { RuntimeFrame, type ThreadRenderProps } from './chat-runtime-frame'
import { currentOrgScope } from '@/lib/org-scope/org-scope-signal'
import { useBrowserDictation } from '@/components/chat/use-browser-dictation'
import { blocksInputRetry } from '@/lib/chat-runs/recovery-error'
import { useChatRuntime } from '@/lib/chat/use-chat-runtime'
import { RunSteerStreamSync } from '@/lib/chat/langgraph-runtime/run-steer-events'
import { useMoldyLangGraphStream } from '@/lib/chat/langgraph-runtime/use-moldy-langgraph-stream'
import { usePublishSubagentRuntime } from '@/lib/chat/langgraph-runtime/subagent-runtime'
import type { ConversationRun, Message, SSEEvent } from '@/lib/types'
import type { User } from '@/lib/types/user'
import type { StreamChatOptions } from '@/lib/sse/stream-chat'
import type { ConversationRuntimeStatus } from '@/lib/stores/chat-navigator-store'
import type { ChatCommandActions } from '@/lib/chat/commands/chat-command-types'
import type { SkillBrief } from '@/lib/types'
import { isActiveRunStatus } from '@/lib/chat-runs/status'

const AgUiRuntimeSection = dynamic(
  () => import('./ag-ui-runtime-section').then((module) => module.AgUiRuntimeSection),
  { ssr: false },
)

type StreamFn = (
  content: string,
  signal: AbortSignal,
  options?: StreamChatOptions,
) => AsyncGenerator<SSEEvent>

export interface ChatRuntimeSectionProps {
  readonly readOnlyConversation?: boolean
  readonly compact?: boolean
  readonly threadHeader?: ReactNode
  readonly activeConversationId: string | null
  readonly activeRun: ConversationRun | null
  readonly agentId: string
  readonly agentImageUrl?: string | null
  readonly agentName?: string
  readonly attachmentAdapter?: AttachmentAdapter
  /** Composer 위 커스텀 힌트 (스킬 빌더 "예시로 시험" 등). */
  readonly composerHint?: ReactNode
  readonly enableRunSteer?: boolean
  readonly emptyContent: ReactNode
  readonly feedbackAdapter?: FeedbackAdapter
  readonly latestRun: ConversationRun | null
  readonly messages: Message[]
  readonly modelName?: string
  /** 메인 v3 채팅 컴포저에 컨텍스트 창 사용량 게이지 표시. */
  readonly showContextGauge?: boolean
  /** 컨텍스트 게이지 한도(agent.model.context_window). null이면 비활성. */
  readonly contextWindow?: number | null
  readonly onRuntimeStatusChange: (status: ConversationRuntimeStatus) => void
  readonly onBeforeNewMessage?: () => void
  readonly onNewMessageAccepted?: () => void
  readonly onStreamEnd: (didMutate: boolean) => void
  readonly streamFn: StreamFn
  readonly totalCost?: number
  readonly useAgUiRuntime?: boolean
  readonly useLangGraphRuntime: boolean
  readonly user?: User | null
  readonly commandActions?: ChatCommandActions
  readonly linkedSkills?: readonly SkillBrief[]
}

export function ChatRuntimeSection({
  readOnlyConversation,
  compact,
  threadHeader,
  activeConversationId,
  activeRun,
  agentId,
  agentImageUrl,
  agentName,
  attachmentAdapter,
  composerHint,
  enableRunSteer = false,
  emptyContent,
  feedbackAdapter,
  latestRun,
  messages,
  modelName,
  showContextGauge,
  contextWindow,
  onRuntimeStatusChange,
  onBeforeNewMessage,
  onNewMessageAccepted,
  onStreamEnd,
  streamFn,
  totalCost,
  useLangGraphRuntime,
  useAgUiRuntime = false,
  user,
  commandActions,
  linkedSkills,
}: ChatRuntimeSectionProps) {
  const dictation = useBrowserDictation()
  const [acceptedSubmission, setAcceptedSubmission] = useState(0)
  const handleNewMessageAccepted = useCallback(() => {
    setAcceptedSubmission((current) => current + 1)
    onNewMessageAccepted?.()
  }, [onNewMessageAccepted])
  const threadProps = useMemo<ThreadRenderProps>(
    () => ({
      compact,
      readOnlyConversation,
      threadHeader,
      agentImageUrl,
      agentName,
      composerHint,
      enableRunSteer,
      conversationId: activeConversationId ?? undefined,
      contextWindow,
      dictationAvailability: dictation.availability,
      emptyContent,
      modelName,
      showContextGauge,
      user,
      commandActions,
      linkedSkills,
      retryLastFailedRunId: latestRun?.status === 'failed' ? latestRun.id : undefined,
      // A delayed run-query refresh must not clear quotes added to the next draft.
      resourceContextResetKey:
        useAgUiRuntime || useLangGraphRuntime
          ? `${activeConversationId ?? 'draft'}:${acceptedSubmission}`
          : `${latestRun?.id ?? 'none'}:${acceptedSubmission}`,
      onDictationStart: dictation.resetFailure,
    }),
    [
      compact,
      readOnlyConversation,
      threadHeader,
      activeConversationId,
      agentImageUrl,
      agentName,
      composerHint,
      enableRunSteer,
      contextWindow,
      dictation.availability,
      dictation.resetFailure,
      emptyContent,
      modelName,
      showContextGauge,
      user,
      commandActions,
      linkedSkills,
      latestRun?.id,
      latestRun?.status,
      acceptedSubmission,
      useLangGraphRuntime,
      useAgUiRuntime,
    ],
  )

  if (useAgUiRuntime && activeConversationId) {
    return (
      <AgUiRuntimeSection
        key={`${currentOrgScope().generation}:${activeConversationId}`}
        agentId={agentId}
        attachmentAdapter={attachmentAdapter}
        conversationId={activeConversationId}
        dictationAdapter={dictation.adapter}
        feedbackAdapter={feedbackAdapter}
        onBeforeNewMessage={onBeforeNewMessage}
        onNewMessageAccepted={handleNewMessageAccepted}
        onRuntimeStatusChange={onRuntimeStatusChange}
        onStreamEnd={onStreamEnd}
        serverActiveRunId={activeRun?.id}
        serverLatestRun={latestRun}
        serverMessages={messages}
        threadProps={threadProps}
      />
    )
  }

  if (useLangGraphRuntime && activeConversationId) {
    return (
      <LangGraphRuntimeSection
        agentId={agentId}
        attachmentAdapter={attachmentAdapter}
        conversationId={activeConversationId}
        dictationAdapter={dictation.adapter}
        feedbackAdapter={feedbackAdapter}
        onBeforeNewMessage={onBeforeNewMessage}
        onNewMessageAccepted={handleNewMessageAccepted}
        onRuntimeStatusChange={onRuntimeStatusChange}
        onStreamEnd={onStreamEnd}
        serverLatestRun={latestRun}
        serverRunIsActive={isActiveRunStatus(activeRun?.status)}
        serverMessages={messages}
        threadProps={threadProps}
      />
    )
  }

  return (
    <LegacyRuntimeSection
      activeConversationId={activeConversationId}
      activeRun={activeRun}
      attachmentAdapter={attachmentAdapter}
      dictationAdapter={dictation.adapter}
      feedbackAdapter={feedbackAdapter}
      latestRun={latestRun}
      messages={messages}
      onStreamEnd={onStreamEnd}
      streamFn={streamFn}
      threadProps={threadProps}
      totalCost={totalCost}
    />
  )
}

interface LegacyRuntimeSectionProps {
  readonly activeConversationId: string | null
  readonly activeRun: ConversationRun | null
  readonly attachmentAdapter?: AttachmentAdapter
  readonly dictationAdapter?: DictationAdapter
  readonly feedbackAdapter?: FeedbackAdapter
  readonly latestRun: ConversationRun | null
  readonly messages: Message[]
  readonly onStreamEnd: (didMutate: boolean) => void
  readonly streamFn: StreamFn
  readonly threadProps: ThreadRenderProps
  readonly totalCost?: number
}

function LegacyRuntimeSection({
  activeConversationId,
  activeRun,
  attachmentAdapter,
  dictationAdapter,
  feedbackAdapter,
  latestRun,
  messages,
  onStreamEnd,
  streamFn,
  threadProps,
  totalCost,
}: LegacyRuntimeSectionProps) {
  const { runtime, onResumeDecisions, registerDecision, partialFailedRunId, partialOutput } =
    useChatRuntime({
      messages,
      totalCost,
      streamFn,
      onStreamEnd,
      conversationId: activeConversationId ?? undefined,
      feedbackAdapter,
      attachmentAdapter,
      dictationAdapter,
      activeRun,
      latestRun,
    })
  const hitlValue = useMemo(
    () => ({ onResumeDecisions, registerDecision }),
    [onResumeDecisions, registerDecision],
  )

  return (
    <RuntimeFrame
      hitlValue={hitlValue}
      runtime={runtime}
      threadProps={threadProps}
      failedRunId={partialFailedRunId}
      partialOutput={partialOutput}
    />
  )
}

interface LangGraphRuntimeSectionProps {
  readonly agentId: string
  readonly attachmentAdapter?: AttachmentAdapter
  readonly dictationAdapter?: DictationAdapter
  readonly conversationId: string
  readonly feedbackAdapter?: FeedbackAdapter
  readonly onBeforeNewMessage?: () => void
  readonly onNewMessageAccepted?: () => void
  readonly onRuntimeStatusChange: (status: ConversationRuntimeStatus) => void
  readonly onStreamEnd: (didMutate: boolean) => void
  readonly serverLatestRun: ConversationRun | null
  readonly serverRunIsActive: boolean
  readonly serverMessages: readonly Message[]
  readonly threadProps: ThreadRenderProps
}

function LangGraphRuntimeSection({
  agentId,
  attachmentAdapter,
  dictationAdapter,
  conversationId,
  feedbackAdapter,
  onBeforeNewMessage,
  onNewMessageAccepted,
  onRuntimeStatusChange,
  onStreamEnd,
  serverLatestRun,
  serverRunIsActive,
  serverMessages,
  threadProps,
}: LangGraphRuntimeSectionProps) {
  const {
    assistantRuntime,
    runtimeIsRunning,
    activities,
    deepAgentsState,
    stream,
    messageQueue,
    retryFailedInput,
    threadRunNotice,
    onResumeDecisions,
    registerDecision,
  } = useMoldyLangGraphStream({
    agentId,
    conversationId,
    feedbackAdapter,
    attachmentAdapter,
    dictationAdapter,
    onBeforeSubmit: onBeforeNewMessage,
    onRunStartAccepted: onNewMessageAccepted,
    serverLatestRun,
    serverRunIsActive,
    serverMessages,
  })
  // Queue submissions can finish entirely through durable replay without a
  // raw SDK loading transition. Observe the same state as the displayed runtime.
  const isRunning = runtimeIsRunning ?? stream.isLoading
  const wasRunningRef = useRef(false)
  const hitlValue = useMemo(
    () => ({ onResumeDecisions, registerDecision, agentId }),
    [onResumeDecisions, registerDecision, agentId],
  )
  usePublishSubagentRuntime(conversationId, stream)

  useEffect(() => {
    if (isRunning) {
      wasRunningRef.current = true
      onRuntimeStatusChange('running')
      return
    }
    onRuntimeStatusChange('idle')
    if (!wasRunningRef.current) return
    wasRunningRef.current = false
    onStreamEnd(false)
  }, [isRunning, onRuntimeStatusChange, onStreamEnd])

  return (
    <>
      {threadProps.enableRunSteer && (
        <RunSteerStreamSync conversationId={conversationId} stream={stream} />
      )}
      <RuntimeFrame
        activities={activities}
        deepAgentsState={deepAgentsState}
        hitlValue={hitlValue}
        runtime={assistantRuntime}
        messageQueue={messageQueue}
        retryFailedInput={retryFailedInput}
        partialOutput={threadRunNotice?.partialOutput}
        failedRunId={
          threadRunNotice?.status === 'failed' && !blocksInputRetry(threadRunNotice.recoveryError)
            ? threadRunNotice.id
            : undefined
        }
        subagentStream={stream}
        threadProps={threadProps}
      />
    </>
  )
}
