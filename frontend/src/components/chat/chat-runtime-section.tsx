'use client'

import { useEffect, useMemo, useRef, type ReactNode } from 'react'
import {
  AssistantRuntimeProvider,
  type AssistantRuntime,
  type AttachmentAdapter,
  type FeedbackAdapter,
} from '@assistant-ui/react'
import { AssistantThread, type AssistantThreadProps } from '@/components/chat/assistant-thread'
import { useChatRuntime } from '@/lib/chat/use-chat-runtime'
import { useMoldyLangGraphStream } from '@/lib/chat/langgraph-runtime/use-moldy-langgraph-stream'
import { HiTLContext, type HiTLContextValue } from '@/lib/chat/hitl-context'
import { ALL_TOOL_UI } from '@/lib/chat/tool-ui-registry'
import type { ConversationRun, Message, SSEEvent } from '@/lib/types'
import type { User } from '@/lib/types/user'
import type { StreamChatOptions } from '@/lib/sse/stream-chat'
import type { ConversationRuntimeStatus } from '@/lib/stores/chat-navigator-store'

type StreamFn = (
  content: string,
  signal: AbortSignal,
  options?: StreamChatOptions,
) => AsyncGenerator<SSEEvent>

type ThreadRenderProps = Pick<
  AssistantThreadProps,
  'agentImageUrl' | 'agentName' | 'conversationId' | 'emptyContent' | 'modelName' | 'user'
>

export interface ChatRuntimeSectionProps {
  readonly activeConversationId: string | null
  readonly activeRun: ConversationRun | null
  readonly agentId: string
  readonly agentImageUrl?: string | null
  readonly agentName?: string
  readonly attachmentAdapter?: AttachmentAdapter
  readonly emptyContent: ReactNode
  readonly feedbackAdapter?: FeedbackAdapter
  readonly latestRun: ConversationRun | null
  readonly messages: Message[]
  readonly modelName?: string
  readonly onRuntimeStatusChange: (status: ConversationRuntimeStatus) => void
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
  emptyContent,
  feedbackAdapter,
  latestRun,
  messages,
  modelName,
  onRuntimeStatusChange,
  onStreamEnd,
  streamFn,
  totalCost,
  useLangGraphRuntime,
  user,
}: ChatRuntimeSectionProps) {
  const threadProps = useMemo<ThreadRenderProps>(
    () => ({
      agentImageUrl,
      agentName,
      conversationId: activeConversationId ?? undefined,
      emptyContent,
      modelName,
      user,
    }),
    [activeConversationId, agentImageUrl, agentName, emptyContent, modelName, user],
  )

  if (useLangGraphRuntime && activeConversationId) {
    return (
      <LangGraphRuntimeSection
        agentId={agentId}
        attachmentAdapter={attachmentAdapter}
        conversationId={activeConversationId}
        feedbackAdapter={feedbackAdapter}
        onRuntimeStatusChange={onRuntimeStatusChange}
        onStreamEnd={onStreamEnd}
        threadProps={threadProps}
      />
    )
  }

  return (
    <LegacyRuntimeSection
      activeConversationId={activeConversationId}
      activeRun={activeRun}
      attachmentAdapter={attachmentAdapter}
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
  readonly conversationId: string
  readonly feedbackAdapter?: FeedbackAdapter
  readonly onRuntimeStatusChange: (status: ConversationRuntimeStatus) => void
  readonly onStreamEnd: (didMutate: boolean) => void
  readonly threadProps: ThreadRenderProps
}

function LangGraphRuntimeSection({
  agentId,
  attachmentAdapter,
  conversationId,
  feedbackAdapter,
  onRuntimeStatusChange,
  onStreamEnd,
  threadProps,
}: LangGraphRuntimeSectionProps) {
  const { assistantRuntime, stream, onResumeDecisions, registerDecision } = useMoldyLangGraphStream(
    {
      agentId,
      conversationId,
      feedbackAdapter,
      attachmentAdapter,
    },
  )
  const wasRunningRef = useRef(false)
  const hitlValue = useMemo(
    () => ({ onResumeDecisions, registerDecision }),
    [onResumeDecisions, registerDecision],
  )

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

  return <RuntimeFrame hitlValue={hitlValue} runtime={assistantRuntime} threadProps={threadProps} />
}

interface RuntimeFrameProps {
  readonly hitlValue: HiTLContextValue
  readonly runtime: AssistantRuntime
  readonly threadProps: ThreadRenderProps
}

function RuntimeFrame({ hitlValue, runtime, threadProps }: RuntimeFrameProps) {
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <HiTLContext.Provider value={hitlValue}>
        <AssistantThread
          {...threadProps}
          showTokenBar
          showMessageTimestamp
          enableAttachments
          toolUI={ALL_TOOL_UI}
        />
      </HiTLContext.Provider>
    </AssistantRuntimeProvider>
  )
}
