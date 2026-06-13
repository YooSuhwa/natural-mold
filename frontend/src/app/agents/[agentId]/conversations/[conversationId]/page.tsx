'use client'

import { use, useEffect, useCallback, useMemo, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { useSetAtom } from 'jotai'
import { useTranslations } from 'next-intl'
import type { Conversation, Message } from '@/lib/types'
import { useAgent } from '@/lib/hooks/use-agents'
import { useSession } from '@/lib/auth/session'
import {
  useMessagesEnvelope,
  useMarkConversationRead,
  conversationKeys,
  invalidateConversationNavigators,
} from '@/lib/hooks/use-conversations'
import { useConversationTitle } from '@/lib/hooks/use-conversation-title'
import { useQueryClient } from '@tanstack/react-query'
import { streamChat, streamStartConversation, type StreamChatOptions } from '@/lib/sse/stream-chat'
import { sessionTokenUsageAtom } from '@/lib/stores/chat-store'
import { chatRightRailAtom, toggleArtifactListRailState } from '@/lib/stores/chat-right-rail'
import {
  conversationRuntimeStatusAtom,
  type ConversationRuntimeStatus,
} from '@/lib/stores/chat-navigator-store'
import { useChatFeedbackAdapter } from '@/lib/chat/feedback-adapter'
import { moldyAttachmentAdapter } from '@/lib/chat/attachment-adapter'
import { getChatRuntimeMode } from '@/lib/chat/runtime-mode'
import { ChatRuntimeSection } from '@/components/chat/chat-runtime-section'
import { ChatEmptyState } from '@/components/chat/chat-empty-state'
import { ChatPageHeader } from '@/components/chat/chat-page-header'
import { AgentSkillsRow } from '@/components/chat/agent-skills-row'
import { ChatRightRail } from '@/components/chat/right-rail/chat-right-rail'
import { Skeleton } from '@/components/ui/skeleton'

const EMPTY_MESSAGES: Message[] = []

export default function ChatPage({
  params,
}: {
  params: Promise<{ agentId: string; conversationId: string }>
}) {
  const { agentId, conversationId } = use(params)
  const router = useRouter()
  const queryClient = useQueryClient()
  const isDraftConversation = conversationId === 'new'
  const startedConversationIdRef = useRef<string | null>(null)
  const { data: agent } = useAgent(agentId)
  const { data: user } = useSession()
  // W7-4 — envelope에서 conversation 누적 비용을 가져와 토큰 바에 흘림. 같은
  // query observer 하나에서 messages와 cost를 함께 파생해 채팅 트리 리렌더를 줄인다.
  const { data: envelope, isLoading: messagesLoading } = useMessagesEnvelope(
    conversationId,
    !isDraftConversation,
  )
  const messages = envelope?.messages ?? EMPTY_MESSAGES
  const markConversationRead = useMarkConversationRead(agentId)
  const { mutate: markRead, isPending: isMarkingRead } = markConversationRead
  const t = useTranslations('chat.page')
  const setSessionTokenUsage = useSetAtom(sessionTokenUsageAtom)
  const setRightRail = useSetAtom(chatRightRailAtom)
  const setConversationRuntimeStatus = useSetAtom(conversationRuntimeStatusAtom)

  // 캐시에서 현재 대화 제목만 추출 (전체 목록 구독 방지)
  const currentConversation = queryClient
    .getQueryData<Conversation[]>(conversationKeys.list(agentId))
    ?.find((c) => c.id === conversationId)
  const resolvedConversationTitle = useConversationTitle(agentId, conversationId, agent?.name)
  const currentTitle = isDraftConversation ? t('newConversation') : resolvedConversationTitle
  const markedReadKeyRef = useRef<string | null>(null)
  const activeConversationId = isDraftConversation ? null : conversationId

  useEffect(() => {
    setSessionTokenUsage({ inputTokens: 0, outputTokens: 0, cost: 0 })
    markedReadKeyRef.current = null
    startedConversationIdRef.current = null
  }, [conversationId, setSessionTokenUsage])

  useEffect(() => {
    if (isDraftConversation) return
    if (messagesLoading || isMarkingRead) return
    const unreadCount = currentConversation?.unread_count ?? 0
    if (unreadCount <= 0) return
    const markReadKey = `${conversationId}:${unreadCount}`
    if (markedReadKeyRef.current === markReadKey) return
    markedReadKeyRef.current = markReadKey
    markRead(conversationId)
  }, [
    conversationId,
    currentConversation?.unread_count,
    isDraftConversation,
    isMarkingRead,
    markRead,
    messagesLoading,
  ])

  const setRuntimeStatus = useCallback(
    (id: string, status: ConversationRuntimeStatus) => {
      setConversationRuntimeStatus((current) => ({ ...current, [id]: status }))
    },
    [setConversationRuntimeStatus],
  )

  const streamFn = useCallback(
    async function* (content: string, signal: AbortSignal, options?: StreamChatOptions) {
      let runtimeConversationId = isDraftConversation ? null : conversationId
      if (runtimeConversationId) setRuntimeStatus(runtimeConversationId, 'running')
      try {
        const stream = !isDraftConversation
          ? streamChat(conversationId, content, signal, options)
          : streamStartConversation(agentId, content, signal, {
              ...options,
              onConversationId: (id) => {
                runtimeConversationId = id
                startedConversationIdRef.current = id
                setRuntimeStatus(id, 'running')
                options?.onConversationId?.(id)
              },
            })
        for await (const event of stream) {
          yield event
        }
      } finally {
        if (runtimeConversationId) setRuntimeStatus(runtimeConversationId, 'idle')
      }
    },
    [agentId, conversationId, isDraftConversation, setRuntimeStatus],
  )

  const onStreamEnd = useCallback(() => {
    // draft에서 시작된 스트림은 ref에 기록된 실제 대화 id로 detail까지 무효화한다
    const settledConversationId = isDraftConversation
      ? startedConversationIdRef.current
      : conversationId
    invalidateConversationNavigators(queryClient, agentId, settledConversationId)
    if (!isDraftConversation) {
      void queryClient.refetchQueries({
        queryKey: conversationKeys.messages(conversationId),
        type: 'active',
      })
      queryClient.invalidateQueries({
        queryKey: conversationKeys.debugTraces(conversationId),
      })
      return
    }
    const createdConversationId = startedConversationIdRef.current
    if (createdConversationId) {
      void queryClient.refetchQueries({
        queryKey: conversationKeys.messages(createdConversationId),
        type: 'active',
      })
      queryClient.invalidateQueries({
        queryKey: conversationKeys.debugTraces(createdConversationId),
      })
      router.replace(`/agents/${agentId}/conversations/${createdConversationId}`)
    }
  }, [queryClient, conversationId, agentId, isDraftConversation, router])

  // P0-1c — current feedback per message id, derived from the messages query.
  // Looked up by ``feedback-adapter`` to decide between POST(upsert) vs DELETE.
  const ratingByMessage = useMemo(() => {
    const map = new Map<string, 'up' | 'down'>()
    for (const m of messages) {
      if (m.feedback?.rating) map.set(m.id, m.feedback.rating)
    }
    return map
  }, [messages])
  const getActiveRating = useCallback((mid: string) => ratingByMessage.get(mid), [ratingByMessage])

  const feedbackAdapter = useChatFeedbackAdapter(conversationId, getActiveRating, () => {
    queryClient.invalidateQueries({ queryKey: conversationKeys.messages(conversationId) })
  })

  const runtimeMode = getChatRuntimeMode()
  const useLangGraphRuntime = runtimeMode === 'langgraph_v3' && activeConversationId !== null

  function handleNewConversation() {
    router.push(`/agents/${agentId}/conversations/new`)
  }
  const handleOpenTrace = useCallback(() => {
    router.push(`/agents/${agentId}/conversations/${conversationId}/traces`)
  }, [agentId, conversationId, router])
  const handleOpenSettings = useCallback(() => {
    router.push(`/agents/${agentId}/settings`)
  }, [agentId, router])
  const handleToggleArtifacts = useCallback(() => {
    setRightRail((current) => toggleArtifactListRailState(current, conversationId))
  }, [conversationId, setRightRail])
  const handleRuntimeStatusChange = useCallback(
    (status: ConversationRuntimeStatus) => {
      if (activeConversationId) setRuntimeStatus(activeConversationId, status)
    },
    [activeConversationId, setRuntimeStatus],
  )

  const emptyContent = <ChatEmptyState agent={agent} fallback={t('emptyState')} />

  return (
    <div className="moldy-app-surface flex min-h-0 flex-1 gap-3 overflow-hidden p-3">
      {/* 메인 채팅 카드 */}
      <section className="moldy-panel flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <ChatPageHeader
          agent={agent}
          agentId={agentId}
          title={currentTitle}
          onNewConversation={handleNewConversation}
          onOpenSettings={handleOpenSettings}
          onOpenTrace={handleOpenTrace}
          onToggleArtifacts={handleToggleArtifacts}
        />

        {/* Agent skills row (P2-10 — visualizes attached skills) */}
        <AgentSkillsRow skills={agent?.skills} />

        {/* Thread */}
        {messagesLoading ? (
          <div className="flex-1 px-4 py-4">
            <div className="mx-auto max-w-3xl space-y-4">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="flex gap-3">
                  <Skeleton className="size-8 rounded-full" />
                  <Skeleton className="moldy-skeleton-message h-16 flex-1" />
                </div>
              ))}
            </div>
          </div>
        ) : (
          <ChatRuntimeSection
            activeConversationId={activeConversationId}
            activeRun={envelope?.active_run ?? null}
            agentId={agentId}
            agentImageUrl={agent?.image_url}
            agentName={agent?.name}
            attachmentAdapter={moldyAttachmentAdapter}
            emptyContent={emptyContent}
            feedbackAdapter={feedbackAdapter}
            latestRun={envelope?.latest_run ?? null}
            messages={messages}
            modelName={agent?.model?.display_name}
            onRuntimeStatusChange={handleRuntimeStatusChange}
            onStreamEnd={onStreamEnd}
            streamFn={streamFn}
            totalCost={envelope?.total_estimated_cost}
            useLangGraphRuntime={useLangGraphRuntime}
            user={user}
          />
        )}
      </section>

      {/* 우측 RightRail — sub-agent / tool-result / outline 패널 슬롯 */}
      <ChatRightRail
        conversationId={activeConversationId}
        className="moldy-panel overflow-hidden"
      />
    </div>
  )
}
