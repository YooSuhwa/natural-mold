'use client'

import { useAtomValue } from 'jotai'
import { useTranslations } from 'next-intl'
import {
  streamingMessageAtom,
  streamingToolCallsAtom,
  isStreamingAtom,
} from '@/lib/stores/chat-store'
import { ToolCallDisplay } from '@/components/chat/tool-call-display'
import { MarkdownContent } from '@/components/chat/markdown-content'
import { AgentAvatar } from '@/components/agent/agent-avatar'

function ThinkingDots() {
  const t = useTranslations('chat.streaming')

  return (
    <div className="flex items-center gap-1.5 px-1">
      <span className="size-2 rounded-full bg-primary/50 animate-pulse [animation-delay:0ms] [animation-duration:1.4s]" />
      <span className="size-2 rounded-full bg-primary/50 animate-pulse [animation-delay:200ms] [animation-duration:1.4s]" />
      <span className="size-2 rounded-full bg-primary/50 animate-pulse [animation-delay:400ms] [animation-duration:1.4s]" />
      <span className="ml-2 text-xs text-muted-foreground animate-pulse [animation-duration:2s]">
        {t('thinking')}
      </span>
    </div>
  )
}

interface StreamingMessageProps {
  agentImageUrl?: string | null
  agentName?: string
}

export function StreamingMessage({ agentImageUrl, agentName }: StreamingMessageProps = {}) {
  const streamingMessage = useAtomValue(streamingMessageAtom)
  const streamingToolCalls = useAtomValue(streamingToolCallsAtom)
  const isStreaming = useAtomValue(isStreamingAtom)

  if (!isStreaming) return null

  const hasContent = streamingMessage?.content
  const hasToolCalls = streamingToolCalls.length > 0

  return (
    <div className="flex gap-3 animate-in fade-in slide-in-from-bottom-2 duration-300">
      <AgentAvatar imageUrl={agentImageUrl ?? null} name={agentName ?? 'Agent'} size="sm" />
      <div className="max-w-[80%] space-y-2">
        {hasToolCalls && (
          <div className="space-y-1.5">
            {streamingToolCalls.map((tc, i) => (
              <ToolCallDisplay
                key={i}
                toolCall={{ name: tc.name, args: tc.params ?? {} }}
                status={tc.status}
                result={tc.result}
                elapsedMs={
                  tc.startedAt && tc.completedAt ? tc.completedAt - tc.startedAt : undefined
                }
              />
            ))}
          </div>
        )}
        {hasContent ? (
          <div className="rounded-2xl bg-muted px-4 py-2.5 text-sm leading-relaxed animate-in fade-in duration-200">
            <MarkdownContent content={streamingMessage.content} />
            <span className="inline-block w-0.5 h-4 animate-pulse bg-primary/60 ml-0.5 rounded-full align-text-bottom" />
          </div>
        ) : (
          !hasToolCalls && (
            <div className="rounded-2xl bg-muted px-4 py-3">
              <ThinkingDots />
            </div>
          )
        )}
      </div>
    </div>
  )
}
