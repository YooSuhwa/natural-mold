"use client"

import { BotIcon, UserIcon } from "lucide-react"
import type { Message } from "@/lib/types"
import { ToolCallDisplay } from "@/components/chat/tool-call-display"
import { MarkdownContent } from "@/components/chat/markdown-content"

interface MessageBubbleProps {
  message: Message
}

export function MessageBubble({ message }: MessageBubbleProps) {
  if (message.role === "tool") {
    return (
      <div className="flex gap-3">
        <div className="w-8" />
        <div className="max-w-[80%] rounded-lg bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
          <span className="font-medium">도구 결과: </span>
          <span className="line-clamp-3">{message.content}</span>
        </div>
      </div>
    )
  }

  const isUser = message.role === "user"

  return (
    <div className={`flex gap-3 ${isUser ? "justify-end" : ""}`}>
      {!isUser && (
        <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
          <BotIcon className="size-4" />
        </div>
      )}
      <div className="max-w-[80%] space-y-2">
        {message.tool_calls && message.tool_calls.length > 0 && (
          <div className="space-y-1">
            {message.tool_calls.map((tc, i) => (
              <ToolCallDisplay key={i} toolCall={tc} status="completed" />
            ))}
          </div>
        )}
        {message.content && (
          <div
            className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
              isUser
                ? "bg-primary text-primary-foreground"
                : "bg-muted"
            }`}
          >
            {isUser ? (
              message.content
            ) : (
              <MarkdownContent content={message.content} />
            )}
          </div>
        )}
      </div>
      {isUser && (
        <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
          <UserIcon className="size-4" />
        </div>
      )}
    </div>
  )
}
