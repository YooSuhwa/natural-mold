'use client'

import { use, useEffect, useRef, useCallback } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useSetAtom } from 'jotai'
import { Settings2Icon, PlusIcon } from 'lucide-react'
import { toast } from 'sonner'
import { useAgent } from '@/lib/hooks/use-agents'
import { useMessages, useCreateConversation } from '@/lib/hooks/use-conversations'
import { useQueryClient } from '@tanstack/react-query'
import { streamChat } from '@/lib/sse/stream-chat'
import {
  streamingMessageAtom,
  streamingToolCallsAtom,
  isStreamingAtom,
} from '@/lib/stores/chat-store'
import type { StreamingToolCall } from '@/lib/stores/chat-store'
import { Button } from '@/components/ui/button'
import { ConversationList } from '@/components/chat/conversation-list'
import { MessageBubble } from '@/components/chat/message-bubble'
import { StreamingMessage } from '@/components/chat/streaming-message'
import { ChatInput } from '@/components/chat/chat-input'
import { Skeleton } from '@/components/ui/skeleton'

export default function ChatPage({
  params,
}: {
  params: Promise<{ agentId: string; conversationId: string }>
}) {
  const { agentId, conversationId } = use(params)
  const router = useRouter()
  const queryClient = useQueryClient()
  const { data: agent } = useAgent(agentId)
  const { data: messages, isLoading: messagesLoading } = useMessages(conversationId)
  const createConversation = useCreateConversation(agentId)

  const setStreamingMessage = useSetAtom(streamingMessageAtom)
  const setStreamingToolCalls = useSetAtom(streamingToolCallsAtom)
  const setIsStreaming = useSetAtom(isStreamingAtom)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  async function handleNewConversation() {
    const conv = await createConversation.mutateAsync(undefined)
    router.push(`/agents/${agentId}/conversations/${conv.id}`)
  }

  async function handleSend(content: string) {
    // Abort previous stream if any
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    setIsStreaming(true)
    setStreamingMessage({ id: '', content: '' })
    setStreamingToolCalls([])
    scrollToBottom()

    try {
      const stream = streamChat(conversationId, content, controller.signal)
      let accumulated = ''
      const toolCalls: StreamingToolCall[] = []

      for await (const event of stream) {
        switch (event.event) {
          case 'content_delta': {
            const delta = event.data.content ?? event.data.delta ?? ''
            accumulated += delta
            setStreamingMessage({ id: '', content: accumulated })
            scrollToBottom()
            break
          }
          case 'tool_call_start': {
            const tc: StreamingToolCall = {
              name: event.data.name ?? 'tool',
              status: 'calling',
              params: event.data.args as Record<string, unknown> | undefined,
              startedAt: Date.now(),
            }
            toolCalls.push(tc)
            setStreamingToolCalls([...toolCalls])
            break
          }
          case 'tool_call_result': {
            const lastTc = toolCalls[toolCalls.length - 1]
            if (lastTc) {
              lastTc.status = 'completed'
              lastTc.result = (event.data.result ?? '') as string
              lastTc.completedAt = Date.now()
              setStreamingToolCalls([...toolCalls])
            }
            break
          }
          case 'message_end': {
            break
          }
          case 'error': {
            toast.error(event.data.message ?? '에이전트 실행 중 오류가 발생했습니다')
            break
          }
        }
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        // User aborted
      }
    } finally {
      setIsStreaming(false)
      setStreamingMessage(null)
      setStreamingToolCalls([])
      // Refresh messages and conversation list (title may have changed)
      queryClient.invalidateQueries({
        queryKey: ['conversations', conversationId, 'messages'],
      })
      queryClient.invalidateQueries({
        queryKey: ['agents', agentId, 'conversations'],
      })
    }
  }

  return (
    <div className="flex flex-1 overflow-hidden">
      {/* Conversation sidebar */}
      <div className="hidden w-72 shrink-0 border-r md:block">
        <ConversationList agentId={agentId} />
      </div>

      {/* Main chat area */}
      <div className="flex flex-1 flex-col">
        {/* Chat header */}
        <div className="flex items-center justify-between border-b px-4 py-2.5">
          <div className="flex items-center gap-2">
            <h1 className="text-sm font-semibold">
              {agent?.name ?? <Skeleton className="h-4 w-24 inline-block" />}
            </h1>
          </div>
          <div className="flex items-center gap-1">
            <Link href={`/agents/${agentId}/settings`}>
              <Button variant="ghost" size="icon-sm">
                <Settings2Icon className="size-4" />
                <span className="sr-only">설정</span>
              </Button>
            </Link>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleNewConversation}
              disabled={createConversation.isPending}
            >
              <PlusIcon className="size-4" data-icon="inline-start" />새 대화
            </Button>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4">
          <div className="mx-auto max-w-2xl space-y-4">
            {messagesLoading ? (
              <div className="space-y-4">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="flex gap-3">
                    <Skeleton className="size-8 rounded-full" />
                    <Skeleton className="h-16 flex-1 rounded-2xl" />
                  </div>
                ))}
              </div>
            ) : messages && messages.length > 0 ? (
              messages.map((msg) => <MessageBubble key={msg.id} message={msg} />)
            ) : (
              <div className="flex flex-col items-center justify-center py-12 text-center">
                <p className="text-sm text-muted-foreground">대화를 시작해보세요.</p>
              </div>
            )}
            <StreamingMessage />
            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input */}
        <div className="border-t p-4">
          <div className="mx-auto max-w-2xl">
            <ChatInput onSend={handleSend} />
          </div>
        </div>
      </div>
    </div>
  )
}
