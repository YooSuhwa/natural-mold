'use client'

import { useEffect, useMemo, useRef, type ReactNode } from 'react'
import {
  AuiConfig,
  AssistantRuntimeProvider,
  type AssistantRuntime,
  type AttachmentAdapter,
  type DictationAdapter,
  type FeedbackAdapter,
} from '@assistant-ui/react'
import type { AnyStream } from '@langchain/react'
import { AssistantThread, type AssistantThreadProps } from '@/components/chat/assistant-thread'
import { useBrowserDictation } from '@/components/chat/use-browser-dictation'
import { useChatRuntime } from '@/lib/chat/use-chat-runtime'
import { useMoldyLangGraphStream } from '@/lib/chat/langgraph-runtime/use-moldy-langgraph-stream'
import {
  SubagentRuntimeProvider,
  usePublishSubagentRuntime,
} from '@/lib/chat/langgraph-runtime/subagent-runtime'
import { HiTLContext, type HiTLContextValue } from '@/lib/chat/hitl-context'
import { ALL_DATA_UI } from '@/lib/chat/data-ui'
import { ALL_TOOLKIT, createMoldyChatTools } from '@/lib/chat/tool-ui-registry'
import type { ConversationRun, Message, SSEEvent } from '@/lib/types'
import type { User } from '@/lib/types/user'
import type { StreamChatOptions } from '@/lib/sse/stream-chat'
import type { ConversationRuntimeStatus } from '@/lib/stores/chat-navigator-store'
import { ServerMessageQueueProvider } from '@/lib/chat/message-queue/server-message-queue-context'
import type { ServerMessageQueueController } from '@/lib/chat/message-queue/server-message-queue-contract'

type StreamFn = (
  content: string,
  signal: AbortSignal,
  options?: StreamChatOptions,
) => AsyncGenerator<SSEEvent>

type ThreadRenderProps = Pick<
  AssistantThreadProps,
  | 'agentImageUrl'
  | 'agentName'
  | 'composerHint'
  | 'conversationId'
  | 'contextWindow'
  | 'dictationAvailability'
  | 'emptyContent'
  | 'modelName'
  | 'showContextGauge'
  | 'user'
  | 'onDictationStart'
>

export interface ChatRuntimeSectionProps {
  readonly activeConversationId: string | null
  readonly activeRun: ConversationRun | null
  readonly agentId: string
  readonly agentImageUrl?: string | null
  readonly agentName?: string
  readonly attachmentAdapter?: AttachmentAdapter
  /** Composer 위 커스텀 힌트 (스킬 빌더 "예시로 시험" 등). */
  readonly composerHint?: ReactNode
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
  readonly useLangGraphRuntime: boolean
  readonly user?: User | null
}

export function ChatRuntimeSection({
  activeConversationId,
  activeRun,
  agentId,
  agentImageUrl,
  agentName,
  attachmentAdapter,
  composerHint,
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
  user,
}: ChatRuntimeSectionProps) {
  const dictation = useBrowserDictation()
  const threadProps = useMemo<ThreadRenderProps>(
    () => ({
      agentImageUrl,
      agentName,
      composerHint,
      conversationId: activeConversationId ?? undefined,
      contextWindow,
      dictationAvailability: dictation.availability,
      emptyContent,
      modelName,
      showContextGauge,
      user,
      onDictationStart: dictation.resetFailure,
    }),
    [
      activeConversationId,
      agentImageUrl,
      agentName,
      composerHint,
      contextWindow,
      dictation.availability,
      dictation.resetFailure,
      emptyContent,
      modelName,
      showContextGauge,
      user,
    ],
  )

  if (useLangGraphRuntime && activeConversationId) {
    return (
      <LangGraphRuntimeSection
        agentId={agentId}
        attachmentAdapter={attachmentAdapter}
        conversationId={activeConversationId}
        dictationAdapter={dictation.adapter}
        feedbackAdapter={feedbackAdapter}
        onBeforeNewMessage={onBeforeNewMessage}
        onNewMessageAccepted={onNewMessageAccepted}
        onRuntimeStatusChange={onRuntimeStatusChange}
        onStreamEnd={onStreamEnd}
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
  const { runtime, onResumeDecisions, registerDecision } = useChatRuntime({
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

  return <RuntimeFrame hitlValue={hitlValue} runtime={runtime} threadProps={threadProps} />
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
  serverMessages,
  threadProps,
}: LangGraphRuntimeSectionProps) {
  const {
    assistantRuntime,
    activities,
    deepAgentsState,
    stream,
    messageQueue,
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
    serverMessages,
  })
  const wasRunningRef = useRef(false)
  const hitlValue = useMemo(
    () => ({ onResumeDecisions, registerDecision }),
    [onResumeDecisions, registerDecision],
  )
  usePublishSubagentRuntime(conversationId, stream)

  useEffect(() => {
    if (stream.isLoading) {
      wasRunningRef.current = true
      onRuntimeStatusChange('running')
      return
    }
    onRuntimeStatusChange('idle')
    if (!wasRunningRef.current) return
    wasRunningRef.current = false
    onStreamEnd(false)
  }, [onRuntimeStatusChange, onStreamEnd, stream.isLoading])

  return (
    <RuntimeFrame
      activities={activities}
      deepAgentsState={deepAgentsState}
      hitlValue={hitlValue}
      runtime={assistantRuntime}
      messageQueue={messageQueue}
      subagentStream={stream}
      threadProps={threadProps}
    />
  )
}

interface RuntimeFrameProps {
  readonly activities?: AssistantThreadProps['activities']
  readonly deepAgentsState?: AssistantThreadProps['deepAgentsState']
  readonly hitlValue: HiTLContextValue
  readonly runtime: AssistantRuntime
  readonly messageQueue?: ServerMessageQueueController
  readonly subagentStream?: AnyStream | null
  readonly threadProps: ThreadRenderProps
}

function RuntimeFrame({
  activities,
  deepAgentsState,
  hitlValue,
  runtime,
  messageQueue,
  subagentStream,
  threadProps,
}: RuntimeFrameProps) {
  const config = AuiConfig({
    tools: createMoldyChatTools(ALL_TOOLKIT, threadProps.conversationId),
  })

  const thread = (
    <HiTLContext.Provider value={hitlValue}>
      <SubagentRuntimeProvider stream={subagentStream}>
        <AssistantThread
          {...threadProps}
          activities={activities}
          dataUI={ALL_DATA_UI}
          deepAgentsState={deepAgentsState}
          showTokenBar
          showMessageTimestamp
          enableAttachments
          enableMessageQueue={Boolean(messageQueue)}
        />
      </SubagentRuntimeProvider>
    </HiTLContext.Provider>
  )

  return (
    <AssistantRuntimeProvider runtime={runtime} config={config}>
      {messageQueue ? (
        <ServerMessageQueueProvider controller={messageQueue}>{thread}</ServerMessageQueueProvider>
      ) : (
        thread
      )}
    </AssistantRuntimeProvider>
  )
}
