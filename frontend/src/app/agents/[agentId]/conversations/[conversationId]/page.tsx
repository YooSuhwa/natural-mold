'use client'

import { use, useEffect, useCallback, useMemo, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { useSetAtom } from 'jotai'
import {
  Settings2Icon,
  SquarePenIcon,
  SparklesIcon,
  MenuIcon,
  MoreHorizontalIcon,
  ActivityIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { AssistantRuntimeProvider, useComposerRuntime } from '@assistant-ui/react'
import type { Agent, Conversation, Message } from '@/lib/types'
import { useAgent } from '@/lib/hooks/use-agents'
import {
  useMessagesEnvelope,
  useCreateConversation,
  useMarkConversationRead,
  conversationKeys,
} from '@/lib/hooks/use-conversations'
import { useQueryClient } from '@tanstack/react-query'
import { streamChat, type StreamChatOptions } from '@/lib/sse/stream-chat'
import { sessionTokenUsageAtom } from '@/lib/stores/chat-store'
import { useChatRuntime } from '@/lib/chat/use-chat-runtime'
import { useChatFeedbackAdapter } from '@/lib/chat/feedback-adapter'
import { moldyAttachmentAdapter } from '@/lib/chat/attachment-adapter'
import { HiTLContext } from '@/lib/chat/hitl-context'
import { ALL_TOOL_UI } from '@/lib/chat/tool-ui-registry'
import { Button } from '@/components/ui/button'
import { ConversationList } from '@/components/chat/conversation-list'
import { AssistantThread } from '@/components/chat/assistant-thread'
import { AgentSkillsRow } from '@/components/chat/agent-skills-row'
import { ChatRightRail } from '@/components/chat/right-rail/chat-right-rail'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { Skeleton } from '@/components/ui/skeleton'
import { Sheet, SheetContent, SheetTrigger } from '@/components/ui/sheet'
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
  const { data: agent } = useAgent(agentId)
  // W7-4 — envelope에서 conversation 누적 비용을 가져와 토큰 바에 흘림. 같은
  // query observer 하나에서 messages와 cost를 함께 파생해 채팅 트리 리렌더를 줄인다.
  const { data: envelope, isLoading: messagesLoading } = useMessagesEnvelope(conversationId)
  const messages = envelope?.messages ?? EMPTY_MESSAGES
  const createConversation = useCreateConversation(agentId)
  const markConversationRead = useMarkConversationRead(agentId)
  const { mutate: markRead, isPending: isMarkingRead } = markConversationRead
  const t = useTranslations('chat.page')
  const setSessionTokenUsage = useSetAtom(sessionTokenUsageAtom)

  // 캐시에서 현재 대화 제목만 추출 (전체 목록 구독 방지)
  const currentConversation = queryClient
    .getQueryData<Conversation[]>(conversationKeys.list(agentId))
    ?.find((c) => c.id === conversationId)
  const currentTitle = currentConversation?.title
  const markedReadKeyRef = useRef<string | null>(null)

  useEffect(() => {
    setSessionTokenUsage({ inputTokens: 0, outputTokens: 0, cost: 0 })
    markedReadKeyRef.current = null
  }, [conversationId, setSessionTokenUsage])

  useEffect(() => {
    if (messagesLoading || isMarkingRead) return
    const unreadCount = currentConversation?.unread_count ?? 0
    if (unreadCount <= 0) return
    const markReadKey = `${conversationId}:${unreadCount}`
    if (markedReadKeyRef.current === markReadKey) return
    markedReadKeyRef.current = markReadKey
    markRead(conversationId)
  }, [conversationId, currentConversation?.unread_count, isMarkingRead, markRead, messagesLoading])

  const streamFn = useCallback(
    (content: string, signal: AbortSignal, options?: StreamChatOptions) =>
      streamChat(conversationId, content, signal, options),
    [conversationId],
  )

  const onStreamEnd = useCallback(() => {
    void queryClient.refetchQueries({
      queryKey: conversationKeys.messages(conversationId),
      type: 'active',
    })
    queryClient.invalidateQueries({
      queryKey: conversationKeys.list(agentId),
    })
    queryClient.invalidateQueries({
      queryKey: conversationKeys.debugTraces(conversationId),
    })
  }, [queryClient, conversationId, agentId])

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
    conversationId,
    feedbackAdapter,
    attachmentAdapter: moldyAttachmentAdapter,
  })

  const hitlValue = useMemo(
    () => ({ onResumeDecisions, registerDecision }),
    [onResumeDecisions, registerDecision],
  )

  async function handleNewConversation() {
    const conv = await createConversation.mutateAsync(undefined)
    router.push(`/agents/${agentId}/conversations/${conv.id}`)
  }

  const emptyContent = <ChatEmptyState agent={agent} fallback={t('emptyState')} />

  return (
    <div className="moldy-app-surface flex min-h-0 flex-1 gap-3 overflow-hidden p-3">
      {/* 좌측 사이드바 카드 (데스크톱) */}
      <aside className="moldy-panel hidden w-72 shrink-0 overflow-hidden md:block">
        <ConversationList
          agentId={agentId}
          agentName={agent?.name}
          agentImageUrl={agent?.image_url ?? null}
          agentDescription={agent?.description ?? null}
        />
      </aside>

      {/* 메인 채팅 카드 */}
      <section className="moldy-panel flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="moldy-panel-header flex items-center justify-between px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2">
            <Sheet>
              <SheetTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="md:hidden"
                    aria-label={t('conversationList')}
                  >
                    <MenuIcon className="size-4" />
                  </Button>
                }
              />
              <SheetContent side="left" className="w-72 p-0">
                <ConversationList
                  agentId={agentId}
                  agentName={agent?.name}
                  agentImageUrl={agent?.image_url ?? null}
                  agentDescription={agent?.description ?? null}
                />
              </SheetContent>
            </Sheet>
            <h1 className="truncate text-sm font-semibold">
              {currentTitle ?? agent?.name ?? <Skeleton className="inline-block h-4 w-24" />}
            </h1>
          </div>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Trace debugger"
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
                <DropdownMenuItem
                  onClick={handleNewConversation}
                  disabled={createConversation.isPending}
                >
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
                modelName={agent?.model?.display_name}
                showTokenBar
                showMessageTimestamp
                enableAttachments
                emptyContent={emptyContent}
                toolUI={ALL_TOOL_UI}
                conversationId={conversationId}
              />
            </HiTLContext.Provider>
          </AssistantRuntimeProvider>
        )}
      </section>

      {/* 우측 RightRail — sub-agent / tool-result / outline 패널 슬롯 */}
      <ChatRightRail
        conversationId={conversationId}
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
