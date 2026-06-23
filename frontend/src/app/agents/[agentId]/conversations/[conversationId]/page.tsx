'use client'

import { use, useEffect, useCallback, useMemo, useRef, useState } from 'react'
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
  upsertConversationNavigatorCache,
} from '@/lib/hooks/use-conversations'
import { conversationsApi } from '@/lib/api/conversations'
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
import {
  CHAT_ROUTE_REPLACED_EVENT,
  isChatRouteReplacedEvent,
  replaceChatRouteWithoutRemount,
} from '@/lib/chat/chat-route-replacement'
import { useLangGraphDraftConversation } from '@/lib/chat/langgraph-runtime/use-langgraph-draft-conversation'
import { ChatRuntimeSection } from '@/components/chat/chat-runtime-section'
import { ChatEmptyState } from '@/components/chat/chat-empty-state'
import { ChatPageHeader } from '@/components/chat/chat-page-header'
import { AgentSkillsRow } from '@/components/chat/agent-skills-row'
import { ChatRightRail } from '@/components/chat/right-rail/chat-right-rail'
import { Skeleton } from '@/components/ui/skeleton'

const EMPTY_MESSAGES: Message[] = []

interface RouteConversationOverride {
  readonly routeKey: string
  readonly conversationId: string
}

interface DraftTitleDetailSuppression {
  readonly routeKey: string
  readonly conversationId: string
}

function conversationIdFromChatPath(pathname: string, agentId: string): string | null {
  const match = /^\/agents\/([^/]+)\/conversations\/([^/]+)$/.exec(pathname)
  if (!match || match[1] !== agentId) return null
  return decodeURIComponent(match[2])
}

export default function ChatPage({
  params,
}: {
  params: Promise<{ agentId: string; conversationId: string }>
}) {
  const { agentId, conversationId } = use(params)
  const router = useRouter()
  const queryClient = useQueryClient()
  const routeKey = `${agentId}:${conversationId}`
  const [routeConversationOverride, setRouteConversationOverride] =
    useState<RouteConversationOverride | null>(null)
  const routeConversationId =
    routeConversationOverride?.routeKey === routeKey
      ? routeConversationOverride.conversationId
      : conversationId
  const isDraftConversation = routeConversationId === 'new'
  const startedConversationIdRef = useRef<string | null>(null)
  const [draftTitleDetailSuppression, setDraftTitleDetailSuppression] =
    useState<DraftTitleDetailSuppression | null>(null)
  const draftTitleDetailSuppressionConversationId =
    draftTitleDetailSuppression?.routeKey === routeKey
      ? draftTitleDetailSuppression.conversationId
      : null
  const [suppressEmptyStateForConversationId, setSuppressEmptyStateForConversationId] = useState<
    string | null
  >(null)
  const { data: agent } = useAgent(agentId)
  const { data: user } = useSession()
  const messageEnvelopeConversationId = routeConversationId
  const shouldLoadMessageEnvelope = !isDraftConversation
  const isPromotedDraftRoute = conversationId === 'new' && !isDraftConversation
  // W7-4 — envelope에서 conversation 누적 비용을 가져와 토큰 바에 흘림. 같은
  // query observer 하나에서 messages와 cost를 함께 파생해 채팅 트리 리렌더를 줄인다.
  const { data: envelope, isLoading: messagesLoading } = useMessagesEnvelope(
    messageEnvelopeConversationId,
    shouldLoadMessageEnvelope,
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
    ?.find((c) => c.id === routeConversationId)
  const markedReadKeyRef = useRef<string | null>(null)
  const runtimeMode = getChatRuntimeMode()

  useEffect(() => {
    const handleRouteReplacement = (event: Event) => {
      if (!isChatRouteReplacedEvent(event)) return
      const replacedConversationId = conversationIdFromChatPath(event.detail.pathname, agentId)
      if (!replacedConversationId) return
      setRouteConversationOverride({
        routeKey,
        conversationId: replacedConversationId,
      })
    }
    const handlePopState = () => setRouteConversationOverride(null)

    window.addEventListener(CHAT_ROUTE_REPLACED_EVENT, handleRouteReplacement)
    window.addEventListener('popstate', handlePopState)
    return () => {
      window.removeEventListener(CHAT_ROUTE_REPLACED_EVENT, handleRouteReplacement)
      window.removeEventListener('popstate', handlePopState)
    }
  }, [agentId, routeKey])

  useEffect(() => {
    setSessionTokenUsage({ inputTokens: 0, outputTokens: 0, cost: 0 })
    markedReadKeyRef.current = null
    startedConversationIdRef.current = null
  }, [agentId, conversationId, setSessionTokenUsage])

  useEffect(() => {
    if (isDraftConversation) return
    if (messagesLoading || isMarkingRead) return
    const unreadCount = currentConversation?.unread_count ?? 0
    if (unreadCount <= 0) return
    const markReadKey = `${routeConversationId}:${unreadCount}`
    if (markedReadKeyRef.current === markReadKey) return
    markedReadKeyRef.current = markReadKey
    markRead(routeConversationId)
  }, [
    currentConversation?.unread_count,
    isDraftConversation,
    isMarkingRead,
    markRead,
    messagesLoading,
    routeConversationId,
  ])

  const setRuntimeStatus = useCallback(
    (id: string, status: ConversationRuntimeStatus) => {
      setConversationRuntimeStatus((current) => ({ ...current, [id]: status }))
    },
    [setConversationRuntimeStatus],
  )
  const handleLangGraphDraftConversationId = useCallback(
    (id: string) => {
      startedConversationIdRef.current = id
      setDraftTitleDetailSuppression({ routeKey, conversationId: id })
    },
    [routeKey],
  )
  const {
    conversationId: langGraphDraftConversationId,
    isBootstrapping: isLangGraphDraftBootstrapping,
    commitDraftConversation,
  } = useLangGraphDraftConversation({
    agentId,
    isDraftConversation,
    runtimeMode,
    onConversationId: handleLangGraphDraftConversationId,
  })
  const activeConversationId = isDraftConversation
    ? langGraphDraftConversationId
    : routeConversationId
  const resolvedSideEffectConversationId = activeConversationId ?? routeConversationId
  const titleConversationId = routeConversationId
  const resolvedConversationTitle = useConversationTitle(
    agentId,
    titleConversationId,
    agent?.name,
    {
      detailEnabled:
        runtimeMode !== 'langgraph_v3' &&
        draftTitleDetailSuppressionConversationId !== titleConversationId &&
        suppressEmptyStateForConversationId !== titleConversationId,
    },
  )
  const currentTitle = isDraftConversation ? t('newConversation') : resolvedConversationTitle

  const streamFn = useCallback(
    async function* (content: string, signal: AbortSignal, options?: StreamChatOptions) {
      let runtimeConversationId = isDraftConversation ? null : routeConversationId
      if (runtimeConversationId) setRuntimeStatus(runtimeConversationId, 'running')
      try {
        const stream = !isDraftConversation
          ? streamChat(routeConversationId, content, signal, options)
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
    [agentId, isDraftConversation, routeConversationId, setRuntimeStatus],
  )
  const promoteDraftRoute = useCallback(
    (createdConversationId: string) => {
      setRouteConversationOverride({
        routeKey,
        conversationId: createdConversationId,
      })
      replaceChatRouteWithoutRemount(`/agents/${agentId}/conversations/${createdConversationId}`)
    },
    [agentId, routeKey],
  )

  const syncPromotedDraftNavigator = useCallback(
    (createdConversationId: string) => {
      void queryClient
        .fetchQuery({
          queryKey: conversationKeys.detail(createdConversationId),
          queryFn: () => conversationsApi.get(createdConversationId),
        })
        .then((conversation) => {
          upsertConversationNavigatorCache(
            queryClient,
            conversation,
            agent
              ? {
                  id: agent.id,
                  name: agent.name,
                  image_url: agent.image_url ?? null,
                }
              : null,
          )
          invalidateConversationNavigators(queryClient, agentId, createdConversationId)
        })
        .catch(() => {
          invalidateConversationNavigators(queryClient, agentId, createdConversationId)
        })
    },
    [agent, agentId, queryClient],
  )

  const onStreamEnd = useCallback(() => {
    // draft에서 시작된 스트림은 ref에 기록된 실제 대화 id로 detail까지 무효화한다
    const settledConversationId = isDraftConversation
      ? startedConversationIdRef.current
      : routeConversationId
    invalidateConversationNavigators(queryClient, agentId, settledConversationId)
    if (settledConversationId) {
      void queryClient.refetchQueries({
        queryKey: conversationKeys.messages(settledConversationId),
        type: 'active',
      })
      queryClient.invalidateQueries({
        queryKey: conversationKeys.debugTraces(settledConversationId),
      })
    }
    if (!isDraftConversation) {
      return
    }
    const createdConversationId = startedConversationIdRef.current
    if (createdConversationId) {
      setSuppressEmptyStateForConversationId(createdConversationId)
      if (runtimeMode !== 'langgraph_v3') {
        router.replace(`/agents/${agentId}/conversations/${createdConversationId}`)
      }
    }
  }, [agentId, isDraftConversation, queryClient, routeConversationId, router, runtimeMode])

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

  const feedbackAdapter = useChatFeedbackAdapter(
    resolvedSideEffectConversationId,
    getActiveRating,
    () => {
      queryClient.invalidateQueries({
        queryKey: conversationKeys.messages(resolvedSideEffectConversationId),
      })
    },
  )
  const useLangGraphRuntime = runtimeMode === 'langgraph_v3' && activeConversationId !== null

  function handleNewConversation() {
    router.push(`/agents/${agentId}/conversations/new`)
  }
  const handleOpenTrace = useCallback(() => {
    router.push(`/agents/${agentId}/conversations/${resolvedSideEffectConversationId}/traces`)
  }, [agentId, resolvedSideEffectConversationId, router])
  const handleOpenSettings = useCallback(() => {
    router.push(`/agents/${agentId}/settings`)
  }, [agentId, router])
  const handleToggleArtifacts = useCallback(() => {
    setRightRail((current) =>
      toggleArtifactListRailState(current, resolvedSideEffectConversationId),
    )
  }, [resolvedSideEffectConversationId, setRightRail])
  const handleRuntimeStatusChange = useCallback(
    (status: ConversationRuntimeStatus) => {
      if (activeConversationId) setRuntimeStatus(activeConversationId, status)
    },
    [activeConversationId, setRuntimeStatus],
  )
  const handleBeforeNewMessage = useCallback(() => {
    if (!isDraftConversation) return
    const committedConversationId =
      commitDraftConversation() ?? langGraphDraftConversationId ?? startedConversationIdRef.current
    if (!committedConversationId) return
    startedConversationIdRef.current = committedConversationId
    setSuppressEmptyStateForConversationId(committedConversationId)
    promoteDraftRoute(committedConversationId)
  }, [
    commitDraftConversation,
    isDraftConversation,
    langGraphDraftConversationId,
    promoteDraftRoute,
  ])
  const handleNewMessageAccepted = useCallback(() => {
    const committedConversationId = startedConversationIdRef.current
    if (!committedConversationId) return
    setSuppressEmptyStateForConversationId(committedConversationId)
    syncPromotedDraftNavigator(committedConversationId)
  }, [syncPromotedDraftNavigator])

  const emptyContent = <ChatEmptyState agent={agent} fallback={t('emptyState')} />
  const shouldSuppressEmptyContent =
    useLangGraphRuntime &&
    ((activeConversationId !== null &&
      suppressEmptyStateForConversationId === activeConversationId) ||
      messages.length > 0)
  const renderedEmptyContent = shouldSuppressEmptyContent ? (
    <div aria-hidden="true" />
  ) : (
    emptyContent
  )

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
        {(!isPromotedDraftRoute && !isDraftConversation && messagesLoading) ||
        isLangGraphDraftBootstrapping ? (
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
            emptyContent={renderedEmptyContent}
            feedbackAdapter={feedbackAdapter}
            latestRun={envelope?.latest_run ?? null}
            messages={messages}
            modelName={agent?.model?.display_name}
            onBeforeNewMessage={handleBeforeNewMessage}
            onNewMessageAccepted={handleNewMessageAccepted}
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
