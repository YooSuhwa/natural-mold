'use client'

import { use, useEffect, useRef, useCallback } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useSetAtom } from 'jotai'
import { Settings2Icon, PlusIcon, BotIcon, SparklesIcon } from 'lucide-react'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'
import { useAgent } from '@/lib/hooks/use-agents'
import { useMessages, useCreateConversation } from '@/lib/hooks/use-conversations'
import { useQueryClient } from '@tanstack/react-query'
import { streamChat } from '@/lib/sse/stream-chat'
import {
  streamingMessageAtom,
  streamingToolCallsAtom,
  isStreamingAtom,
  sessionTokenUsageAtom,
  lastMessageTokensAtom,
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
  const t = useTranslations('chat.page')

  const setStreamingMessage = useSetAtom(streamingMessageAtom)
  const setStreamingToolCalls = useSetAtom(streamingToolCallsAtom)
  const setIsStreaming = useSetAtom(isStreamingAtom)
  const setSessionTokenUsage = useSetAtom(sessionTokenUsageAtom)
  const setLastMessageTokens = useSetAtom(lastMessageTokensAtom)
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
            // Backend sends { prompt_tokens, completion_tokens } in usage.
            // estimated_cost is not yet provided by the backend — defaults to 0.
            const usage = event.data.usage
            if (usage) {
              const input = usage.prompt_tokens ?? usage.input_tokens ?? 0
              const output = usage.completion_tokens ?? usage.output_tokens ?? 0
              const cost = usage.estimated_cost ?? 0
              const msgTokens = { inputTokens: input, outputTokens: output, cost }
              setLastMessageTokens(msgTokens)
              setSessionTokenUsage((prev) => ({
                inputTokens: prev.inputTokens + input,
                outputTokens: prev.outputTokens + output,
                cost: prev.cost + cost,
              }))
            }
            break
          }
          case 'error': {
            toast.error(event.data.message ?? t('error'))
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

  const hasMessages = messages && messages.length > 0

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
                <span className="sr-only">{t('settings')}</span>
              </Button>
            </Link>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleNewConversation}
              disabled={createConversation.isPending}
            >
              <PlusIcon className="size-4" data-icon="inline-start" />
              {t('newConversation')}
            </Button>
          </div>
        </div>

        {/* Messages */}
        <div className="relative flex-1 overflow-y-auto">
          {/* Top gradient fade */}
          <div className="pointer-events-none sticky top-0 z-10 h-6 bg-gradient-to-b from-background to-transparent" />

          <div className="px-4 pb-4">
            <div className="mx-auto max-w-3xl">
              {messagesLoading ? (
                <div className="space-y-4">
                  {Array.from({ length: 3 }).map((_, i) => (
                    <div key={i} className="flex gap-3">
                      <Skeleton className="size-8 rounded-full" />
                      <Skeleton className="h-16 flex-1 rounded-2xl" />
                    </div>
                  ))}
                </div>
              ) : hasMessages ? (
                (() => {
                  // Build a map of tool_call_id → parsed tool result content
                  const toolResultMap = new Map<string, string>()
                  for (const msg of messages) {
                    if (msg.role === 'tool' && msg.tool_call_id) {
                      toolResultMap.set(msg.tool_call_id, msg.content)
                    }
                  }
                  // Track which tool messages are absorbed into ToolCallDisplay
                  const absorbedToolMsgIds = new Set<string>()
                  for (const msg of messages) {
                    if (msg.role === 'assistant' && msg.tool_calls) {
                      for (const tc of msg.tool_calls) {
                        if (tc.id && toolResultMap.has(tc.id)) {
                          // Find the tool message and mark it absorbed
                          const toolMsg = messages.find(
                            (m) => m.role === 'tool' && m.tool_call_id === tc.id,
                          )
                          if (toolMsg) absorbedToolMsgIds.add(toolMsg.id)
                        }
                      }
                    }
                  }
                  // Filter out absorbed tool messages for previousRole calculation
                  const visibleMessages = messages.filter((msg) => !absorbedToolMsgIds.has(msg.id))
                  return visibleMessages.map((msg, idx) => (
                    <MessageBubble
                      key={msg.id}
                      message={msg}
                      previousRole={idx > 0 ? visibleMessages[idx - 1].role : null}
                      toolResultMap={toolResultMap}
                    />
                  ))
                })()
              ) : (
                <div className="flex flex-col items-center justify-center py-20 text-center">
                  <div className="flex size-14 items-center justify-center rounded-2xl bg-primary/10 text-primary mb-4">
                    <BotIcon className="size-7" />
                  </div>
                  <h2 className="text-lg font-semibold mb-1">{agent?.name ?? t('emptyState')}</h2>
                  {agent?.description && (
                    <p className="text-sm text-muted-foreground max-w-md mb-4">
                      {agent.description}
                    </p>
                  )}
                  <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <SparklesIcon className="size-3.5" />
                    <span>{t('emptyState')}</span>
                  </div>
                </div>
              )}
              <StreamingMessage />
              <div ref={messagesEndRef} />
            </div>
          </div>

          {/* Bottom gradient fade */}
          <div className="pointer-events-none sticky bottom-0 z-10 h-6 bg-gradient-to-t from-background to-transparent" />
        </div>

        {/* Input */}
        <div className="border-t p-4">
          <div className="mx-auto max-w-3xl">
            <ChatInput onSend={handleSend} modelName={agent?.model?.display_name} />
          </div>
        </div>
      </div>
    </div>
  )
}
