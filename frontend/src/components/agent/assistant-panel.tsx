'use client'

import { memo, useState, useRef, useEffect, useCallback, useMemo } from 'react'
import {
  SparklesIcon,
  SendIcon,
  Loader2Icon,
  UserIcon,
  CheckCircle2Icon,
  XCircleIcon,
  WrenchIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  AlertCircleIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import { useTranslations } from 'next-intl'
import { useQueryClient } from '@tanstack/react-query'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { MarkdownContent } from '@/components/chat/markdown-content'
import { cn } from '@/lib/utils'
import { streamAssistant } from '@/lib/sse/stream-assistant'
import { AgentAvatar } from '@/components/agent/agent-avatar'
import type { AssistantToolCallResult } from '@/lib/types'

interface AssistantMessage {
  id: string
  role: 'user' | 'assistant' | 'error'
  content: string
  toolCalls: AssistantToolCallResult[]
  isStreaming?: boolean
}

interface AssistantPanelProps {
  agentId: string
  agentName: string
  agentImageUrl?: string | null
}

function ToolCallBadge({ call }: { call: AssistantToolCallResult }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="mt-1.5">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5"
      >
        <Badge
          variant="outline"
          className={cn(
            'text-xs gap-1 cursor-pointer transition-colors',
            call.success
              ? 'border-emerald-500/30 text-emerald-600 hover:bg-emerald-500/5'
              : 'border-destructive/30 text-destructive hover:bg-destructive/5',
          )}
        >
          {call.success ? (
            <CheckCircle2Icon className="size-3" />
          ) : (
            <XCircleIcon className="size-3" />
          )}
          <WrenchIcon className="size-3" />
          {call.tool_name}
          {expanded ? <ChevronUpIcon className="size-3" /> : <ChevronDownIcon className="size-3" />}
        </Badge>
      </button>
      {expanded && call.summary && (
        <div className="mt-1 ml-1 rounded-md bg-muted/50 px-2.5 py-1.5 text-xs text-muted-foreground">
          {call.summary}
        </div>
      )}
    </div>
  )
}

const MessageBubble = memo(function MessageBubble({ message, agentImageUrl, agentName }: { message: AssistantMessage; agentImageUrl?: string | null; agentName?: string }) {
  const t = useTranslations('agent.assistant')

  if (message.role === 'error') {
    return (
      <div className="flex gap-2.5" role="alert">
        <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-destructive/10">
          <AlertCircleIcon className="size-3.5 text-destructive" />
        </div>
        <div className="max-w-[85%]">
          <div className="rounded-2xl bg-destructive/5 border border-destructive/20 px-3.5 py-2 text-sm leading-relaxed text-destructive">
            {message.content || t('error.generic')}
          </div>
        </div>
      </div>
    )
  }

  if (message.role === 'user') {
    return (
      <div className="flex gap-2.5 justify-end">
        <div className="max-w-[85%]">
          <div className="rounded-2xl px-3.5 py-2 text-sm leading-relaxed bg-primary text-primary-foreground">
            {message.content}
          </div>
        </div>
        <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted">
          <UserIcon className="size-3.5" />
        </div>
      </div>
    )
  }

  return (
    <div className="flex gap-2.5">
      <AgentAvatar imageUrl={agentImageUrl ?? null} name={agentName ?? 'Agent'} size="xs" />
      <div className="max-w-[85%] space-y-1">
        <div className="rounded-2xl bg-muted px-3.5 py-2 text-sm leading-relaxed">
          {message.content ? (
            <MarkdownContent content={message.content} />
          ) : message.isStreaming ? (
            <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
          ) : null}
        </div>
        {message.toolCalls.map((call, i) => (
          <ToolCallBadge key={`${call.tool_name}-${i}`} call={call} />
        ))}
      </div>
    </div>
  )
})

export function AssistantPanel({ agentId, agentName, agentImageUrl }: AssistantPanelProps) {
  const t = useTranslations('agent.assistant')
  const ts = useTranslations('agent.suggestion')
  // Stable session ID per panel instance — each tab/mount gets its own conversation
  const sessionId = useMemo(() => crypto.randomUUID(), [])
  const [messages, setMessages] = useState<AssistantMessage[]>([])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const isComposingRef = useRef(false)
  const qc = useQueryClient()

  const scrollToBottom = useCallback(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
    }
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const handleSend = useCallback(async () => {
    const text = input.trim()
    if (!text || isLoading) return

    setInput('')
    const userMessage: AssistantMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: text,
      toolCalls: [],
    }
    const assistantId = crypto.randomUUID()
    setMessages((prev) => [
      ...prev,
      userMessage,
      { id: assistantId, role: 'assistant', content: '', toolCalls: [], isStreaming: true },
    ])
    setIsLoading(true)

    const abort = new AbortController()
    abortRef.current = abort

    let streamedContent = ''
    const collectedToolCalls: AssistantToolCallResult[] = []

    try {
      for await (const event of streamAssistant(agentId, text, abort.signal, sessionId)) {
        switch (event.event) {
          case 'content_delta': {
            const delta = event.data.delta ?? event.data.content ?? ''
            streamedContent += delta
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, content: streamedContent, isStreaming: true } : m,
              ),
            )
            break
          }
          case 'tool_call_start': {
            collectedToolCalls.push({
              tool_name: event.data.tool_name,
              success: true,
              summary: '',
            })
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? { ...m, toolCalls: [...collectedToolCalls] } : m,
              ),
            )
            break
          }
          case 'tool_call_result': {
            if (collectedToolCalls.length > 0) {
              const last = collectedToolCalls[collectedToolCalls.length - 1]
              collectedToolCalls[collectedToolCalls.length - 1] = {
                ...last,
                summary: event.data.result,
              }
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, toolCalls: [...collectedToolCalls] } : m,
                ),
              )
            }
            break
          }
          case 'message_end': {
            const finalContent = event.data.content || streamedContent
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      content: finalContent,
                      toolCalls: [...collectedToolCalls],
                      isStreaming: false,
                    }
                  : m,
              ),
            )
            break
          }
          case 'error': {
            toast.error(event.data.message)
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId
                  ? {
                      ...m,
                      role: 'error' as const,
                      content: event.data.message,
                      isStreaming: false,
                    }
                  : m,
              ),
            )
            break
          }
        }
      }

      // Finalize if message_end wasn't received
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId && m.isStreaming ? { ...m, isStreaming: false } : m)),
      )

      // Invalidate agent queries (Assistant may have modified the agent)
      if (collectedToolCalls.length > 0) {
        qc.invalidateQueries({ queryKey: ['agents'] })
        qc.invalidateQueries({ queryKey: ['agents', agentId] })
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return
      toast.error(t('toast.failed'))
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, role: 'error' as const, content: t('error.generic'), isStreaming: false }
            : m,
        ),
      )
    } finally {
      setIsLoading(false)
      abortRef.current = null
    }
  }, [input, isLoading, agentId, sessionId, qc, t])

  const SUGGESTIONS = [ts('polite'), ts('concise'), ts('cost'), ts('addSearch')]

  return (
    <div className="flex flex-col rounded-xl border bg-background">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <SparklesIcon className="size-4 text-primary" />
        <h3 className="text-sm font-semibold">{t('title')}</h3>
        <span className="text-xs text-muted-foreground">{t('description', { agentName })}</span>
      </div>

      <div
        ref={scrollRef}
        className="flex-1 overflow-auto px-4 py-4 space-y-4 min-h-[300px] max-h-[500px]"
      >
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center text-muted-foreground py-8">
            <SparklesIcon className="size-8 mb-3 text-primary/40" />
            <p className="text-sm font-medium">{t('emptyState')}</p>
            <div
              className="mt-3 flex flex-wrap justify-center gap-2"
              role="group"
              aria-label={t('emptyState')}
            >
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  className="rounded-full border px-3 py-1 text-xs hover:bg-accent transition-colors"
                  onClick={() => setInput(suggestion)}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} agentImageUrl={agentImageUrl} agentName={agentName} />
        ))}
      </div>

      <div className="border-t px-4 py-3">
        <div className="flex items-end gap-2">
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
                e.preventDefault()
                handleSend()
              }
            }}
            onCompositionStart={() => {
              isComposingRef.current = true
            }}
            onCompositionEnd={() => {
              isComposingRef.current = false
            }}
            placeholder={t('inputPlaceholder')}
            rows={1}
            className="min-h-[40px] max-h-[120px] resize-none text-sm"
          />
          <Button
            size="icon"
            onClick={handleSend}
            disabled={!input.trim() || isLoading}
            className="shrink-0"
            aria-label={t('send')}
          >
            {isLoading ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <SendIcon className="size-4" />
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}
