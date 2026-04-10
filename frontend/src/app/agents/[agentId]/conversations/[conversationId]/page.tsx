'use client'

import { use, useEffect, useCallback, useState, useMemo } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useSetAtom } from 'jotai'
import { Settings2Icon, SquarePenIcon, SparklesIcon, MenuIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { AssistantRuntimeProvider } from '@assistant-ui/react'
import { useAgent } from '@/lib/hooks/use-agents'
import { useMessages, useCreateConversation, conversationKeys } from '@/lib/hooks/use-conversations'
import { useQueryClient } from '@tanstack/react-query'
import { streamChat } from '@/lib/sse/stream-chat'
import { sessionTokenUsageAtom } from '@/lib/stores/chat-store'
import { useChatRuntime } from '@/lib/chat/use-chat-runtime'
import { HiTLContext } from '@/lib/chat/hitl-context'
import { ALL_TOOL_UI } from '@/lib/chat/tool-ui-registry'
import { Button } from '@/components/ui/button'
import { ConversationList } from '@/components/chat/conversation-list'
import { AssistantThread } from '@/components/chat/assistant-thread'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import { Skeleton } from '@/components/ui/skeleton'
import { Sheet, SheetContent, SheetTrigger } from '@/components/ui/sheet'

export default function ChatPage({
  params,
}: {
  params: Promise<{ agentId: string; conversationId: string }>
}) {
  const { agentId, conversationId } = use(params)
  const router = useRouter()
  const queryClient = useQueryClient()
  const { data: agent } = useAgent(agentId)
  const { data: messages = [], isLoading: messagesLoading } = useMessages(conversationId)
  const createConversation = useCreateConversation(agentId)
  const t = useTranslations('chat.page')
  const [showConversationList, setShowConversationList] = useState(true)
  const setSessionTokenUsage = useSetAtom(sessionTokenUsageAtom)

  // 캐시에서 현재 대화 제목만 추출 (전체 목록 구독 방지)
  const currentTitle = queryClient
    .getQueryData<{ id: string; title?: string | null }[]>(conversationKeys.list(agentId))
    ?.find((c) => c.id === conversationId)?.title

  // Reset token usage when conversation changes
  useEffect(() => {
    setSessionTokenUsage({ inputTokens: 0, outputTokens: 0, cost: 0 })
  }, [conversationId, setSessionTokenUsage])

  // streamFn: conversationId를 바인딩한 SSE 스트리밍 함수
  const streamFn = useCallback(
    (content: string, signal: AbortSignal) => streamChat(conversationId, content, signal),
    [conversationId],
  )

  // 스트리밍 완료 후 처리: 메시지 + 대화 목록 갱신, 토큰 추적
  const onStreamEnd = useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: conversationKeys.messages(conversationId),
    })
    queryClient.invalidateQueries({
      queryKey: conversationKeys.list(agentId),
    })
  }, [queryClient, conversationId, agentId])

  const { runtime, onResume } = useChatRuntime({
    messages,
    streamFn,
    onStreamEnd,
    conversationId,
  })

  async function handleNewConversation() {
    const conv = await createConversation.mutateAsync(undefined)
    router.push(`/agents/${agentId}/conversations/${conv.id}`)
  }

  // 빈 상태 커스텀 콘텐츠
  const emptyContent = useMemo(
    () => (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <div className="mb-4">
          <AgentAvatar imageUrl={agent?.image_url ?? null} name={agent?.name ?? 'Agent'} size="lg" />
        </div>
        <h2 className="mb-1 text-lg font-semibold">{agent?.name ?? t('emptyState')}</h2>
        {agent?.description && (
          <p className="mb-4 max-w-md text-sm text-muted-foreground">{agent.description}</p>
        )}
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <SparklesIcon className="size-3.5" />
          <span>{t('emptyState')}</span>
        </div>
      </div>
    ),
    [agent, t],
  )

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Conversation sidebar — desktop toggleable */}
      {showConversationList && (
        <div className="hidden w-72 shrink-0 border-r md:block">
          <ConversationList
            agentId={agentId}
            agentName={agent?.name}
            onClose={() => setShowConversationList(false)}
          />
        </div>
      )}

      {/* Main chat area */}
      <div className="flex flex-1 flex-col">
        {/* Chat header */}
        <div className="flex items-center justify-between border-b px-4 py-2.5">
          <div className="flex items-center gap-2">
            {/* Mobile conversation list trigger */}
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
                <ConversationList agentId={agentId} agentName={agent?.name} />
              </SheetContent>
            </Sheet>
            {/* Desktop: show hamburger when conversation list is hidden */}
            {!showConversationList && (
              <Button
                variant="ghost"
                size="icon-sm"
                className="hidden md:inline-flex"
                onClick={() => setShowConversationList(true)}
                aria-label={t('conversationList')}
              >
                <MenuIcon className="size-4" />
              </Button>
            )}
            <h1 className="truncate text-sm font-semibold">
              {currentTitle ?? agent?.name ?? <Skeleton className="inline-block h-4 w-24" />}
            </h1>
          </div>
          {!showConversationList && (
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                onClick={handleNewConversation}
                disabled={createConversation.isPending}
              >
                <SquarePenIcon className="size-4" data-icon="inline-start" />
                {t('newConversation')}
              </Button>
              <Link href={`/agents/${agentId}/settings`}>
                <Button variant="ghost" size="sm">
                  <Settings2Icon className="size-4" data-icon="inline-start" />
                  {t('settings')}
                </Button>
              </Link>
            </div>
          )}
        </div>

        {/* Thread — assistant-ui 런타임으로 교체 */}
        {messagesLoading ? (
          <div className="flex-1 px-4 py-4">
            <div className="mx-auto max-w-3xl space-y-4">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="flex gap-3">
                  <Skeleton className="size-8 rounded-full" />
                  <Skeleton className="h-16 flex-1 rounded-2xl" />
                </div>
              ))}
            </div>
          </div>
        ) : (
          <AssistantRuntimeProvider runtime={runtime}>
            <HiTLContext.Provider value={{ onResume }}>
              <AssistantThread
                agentImageUrl={agent?.image_url}
                agentName={agent?.name}
                modelName={agent?.model?.display_name}
                showTokenBar
                emptyContent={emptyContent}
                toolUI={ALL_TOOL_UI}
              />
            </HiTLContext.Provider>
          </AssistantRuntimeProvider>
        )}
      </div>
    </div>
  )
}
