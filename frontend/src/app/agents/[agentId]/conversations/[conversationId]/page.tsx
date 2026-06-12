'use client'

import { use, useEffect, useCallback, useMemo, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { useSetAtom } from 'jotai'
import {
  Settings2Icon,
  SquarePenIcon,
  SparklesIcon,
  MoreHorizontalIcon,
  ActivityIcon,
  FilesIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { AssistantRuntimeProvider, useComposerRuntime } from '@assistant-ui/react'
import type { Agent, Conversation, Message } from '@/lib/types'
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
import { useChatRuntime } from '@/lib/chat/use-chat-runtime'
import { useChatFeedbackAdapter } from '@/lib/chat/feedback-adapter'
import { moldyAttachmentAdapter } from '@/lib/chat/attachment-adapter'
import { HiTLContext } from '@/lib/chat/hitl-context'
import { ALL_TOOL_UI } from '@/lib/chat/tool-ui-registry'
import { Button } from '@/components/ui/button'
import { AssistantThread } from '@/components/chat/assistant-thread'
import { AgentSkillsRow } from '@/components/chat/agent-skills-row'
import { ChatRightRail } from '@/components/chat/right-rail/chat-right-rail'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { AgentContextPopover } from '@/components/agent/agent-context-popover'
import { Skeleton } from '@/components/ui/skeleton'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu'

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

  const { runtime, onResumeDecisions, registerDecision } = useChatRuntime({
    messages,
    totalCost: envelope?.total_estimated_cost,
    streamFn,
    onStreamEnd,
    conversationId: activeConversationId ?? undefined,
    feedbackAdapter,
    attachmentAdapter: moldyAttachmentAdapter,
    activeRun: envelope?.active_run ?? null,
    latestRun: envelope?.latest_run ?? null,
  })

  const hitlValue = useMemo(
    () => ({ onResumeDecisions, registerDecision }),
    [onResumeDecisions, registerDecision],
  )

  function handleNewConversation() {
    router.push(`/agents/${agentId}/conversations/new`)
  }

  const emptyContent = <ChatEmptyState agent={agent} fallback={t('emptyState')} />

  return (
    <div className="moldy-app-surface flex min-h-0 flex-1 gap-3 overflow-hidden p-3">
      {/* 메인 채팅 카드 */}
      <section className="moldy-panel flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <div className="moldy-panel-header flex items-center justify-between px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <h1 className="truncate text-sm font-semibold">
              {currentTitle ?? agent?.name ?? <Skeleton className="inline-block h-4 w-24" />}
            </h1>
            <AgentContextPopover agent={agent} agentId={agentId} />
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t('artifacts')}
              onClick={() =>
                setRightRail((current) => toggleArtifactListRailState(current, conversationId))
              }
            >
              <FilesIcon className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={t('traceDebugger')}
              onClick={() =>
                router.push(`/agents/${agentId}/conversations/${conversationId}/traces`)
              }
            >
              <ActivityIcon className="size-4" />
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={<Button variant="ghost" size="icon-sm" aria-label={t('menu')} />}
              >
                <MoreHorizontalIcon className="size-4" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={handleNewConversation}>
                  <SquarePenIcon />
                  {t('newConversation')}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => router.push(`/agents/${agentId}/settings`)}>
                  <Settings2Icon />
                  {t('settings')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>

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
          <AssistantRuntimeProvider runtime={runtime}>
            <HiTLContext.Provider value={hitlValue}>
              <AssistantThread
                agentImageUrl={agent?.image_url}
                agentName={agent?.name}
                user={user}
                modelName={agent?.model?.display_name}
                showTokenBar
                showMessageTimestamp
                enableAttachments
                emptyContent={emptyContent}
                toolUI={ALL_TOOL_UI}
                conversationId={activeConversationId ?? undefined}
              />
            </HiTLContext.Provider>
          </AssistantRuntimeProvider>
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

interface ChatEmptyStateProps {
  agent: Agent | undefined
  fallback: string
}

function ChatEmptyState({ agent, fallback }: ChatEmptyStateProps) {
  const t = useTranslations('chat')
  // AssistantRuntimeProvider 컨텍스트 안에서만 동작 — emptyContent는 provider 자식
  const composer = useComposerRuntime({ optional: true })
  const openerQuestions = agent?.opener_questions ?? []

  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      <div className="mb-4">
        <AgentAvatar
          imageUrl={agent?.image_url ?? null}
          name={agent?.name ?? t('defaultAgentName')}
          size="lg"
        />
      </div>
      <h2 className="mb-1 text-lg font-semibold">{agent?.name ?? fallback}</h2>
      {agent?.description && (
        <p className="mb-4 max-w-md text-sm text-muted-foreground">{agent.description}</p>
      )}
      <div className="flex items-center gap-1.5 rounded-full bg-primary px-3 py-1 text-xs font-medium text-primary-foreground ring-1 ring-primary-strong/15">
        <SparklesIcon className="size-3.5" />
        <span>{fallback}</span>
      </div>
      {openerQuestions.length > 0 && (
        <div className="mt-6 flex max-w-2xl flex-wrap justify-center gap-2">
          {openerQuestions.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => composer?.setText(q)}
              className="rounded-full border border-primary-strong/20 bg-background/80 px-3 py-1.5 text-xs transition-colors hover:bg-primary hover:text-primary-foreground"
            >
              {q}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
