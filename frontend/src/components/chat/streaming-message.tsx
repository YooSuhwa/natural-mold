'use client'

import { useAtomValue } from 'jotai'
import { BotIcon } from 'lucide-react'
import {
  streamingMessageAtom,
  streamingToolCallsAtom,
  isStreamingAtom,
} from '@/lib/stores/chat-store'
import { ToolCallDisplay } from '@/components/chat/tool-call-display'
import { MarkdownContent } from '@/components/chat/markdown-content'

function ThinkingDots() {
  return (
    <div className="flex items-center gap-1 px-1">
      <span className="size-1.5 rounded-full bg-muted-foreground/60 animate-bounce [animation-delay:0ms]" />
      <span className="size-1.5 rounded-full bg-muted-foreground/60 animate-bounce [animation-delay:150ms]" />
      <span className="size-1.5 rounded-full bg-muted-foreground/60 animate-bounce [animation-delay:300ms]" />
      <span className="ml-2 text-xs text-muted-foreground">생각 중...</span>
    </div>
  )
}

export function StreamingMessage() {
  const streamingMessage = useAtomValue(streamingMessageAtom)
  const streamingToolCalls = useAtomValue(streamingToolCallsAtom)
  const isStreaming = useAtomValue(isStreamingAtom)

  if (!isStreaming) return null

  const hasContent = streamingMessage?.content
  const hasToolCalls = streamingToolCalls.length > 0

  return (
    <div className="flex gap-3">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <BotIcon className="size-4" />
      </div>
      <div className="max-w-[80%] space-y-2">
        {hasToolCalls && (
          <div className="space-y-1">
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
          <div className="rounded-2xl bg-muted px-4 py-2.5 text-sm leading-relaxed">
            <MarkdownContent content={streamingMessage.content} />
            <span className="inline-block w-1 animate-pulse bg-foreground/40 ml-0.5">&nbsp;</span>
          </div>
        ) : (
          <div className="rounded-2xl bg-muted px-4 py-3">
            <ThinkingDots />
          </div>
        )}
      </div>
    </div>
  )
}
