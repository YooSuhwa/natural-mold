"use client"

import { useAtomValue } from "jotai"
import { BotIcon, Loader2Icon } from "lucide-react"
import {
  streamingMessageAtom,
  streamingToolCallsAtom,
  isStreamingAtom,
} from "@/lib/stores/chat-store"
import { ToolCallDisplay } from "@/components/chat/tool-call-display"
import { MarkdownContent } from "@/components/chat/markdown-content"

export function StreamingMessage() {
  const streamingMessage = useAtomValue(streamingMessageAtom)
  const streamingToolCalls = useAtomValue(streamingToolCallsAtom)
  const isStreaming = useAtomValue(isStreamingAtom)

  if (!isStreaming) return null

  return (
    <div className="flex gap-3">
      <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
        <BotIcon className="size-4" />
      </div>
      <div className="max-w-[80%] space-y-2">
        {streamingToolCalls.length > 0 && (
          <div className="space-y-1">
            {streamingToolCalls.map((tc, i) => (
              <ToolCallDisplay
                key={i}
                toolCall={{ name: tc.name, args: tc.params ?? {} }}
                status={tc.status}
                result={tc.result}
              />
            ))}
          </div>
        )}
        {streamingMessage?.content ? (
          <div className="rounded-2xl bg-muted px-4 py-2.5 text-sm leading-relaxed">
            <MarkdownContent content={streamingMessage.content} />
            <span className="inline-block w-1 animate-pulse bg-foreground/40 ml-0.5">
              &nbsp;
            </span>
          </div>
        ) : (
          <div className="rounded-2xl bg-muted px-4 py-3">
            <Loader2Icon className="size-4 animate-spin text-muted-foreground" />
          </div>
        )}
      </div>
    </div>
  )
}
