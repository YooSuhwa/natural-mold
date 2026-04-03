'use client'

import { useState } from 'react'
import { BotIcon, UserIcon, CopyIcon, CheckIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { Message } from '@/lib/types'
import { ToolCallDisplay } from '@/components/chat/tool-call-display'
import { MarkdownContent } from '@/components/chat/markdown-content'

interface MessageBubbleProps {
  message: Message
  tokenInfo?: { tokens: number; cost: number } | null
}

export function MessageBubble({ message, tokenInfo }: MessageBubbleProps) {
  const [copied, setCopied] = useState(false)
  const t = useTranslations('chat.message')

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(message.content)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard API may not be available
    }
  }

  if (message.role === 'tool') {
    return (
      <div className="flex gap-3">
        <div className="w-8" />
        <div className="max-w-[80%] rounded-lg bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
          <span className="font-medium">{t('toolResult')}</span>
          <span className="line-clamp-3">{message.content}</span>
        </div>
      </div>
    )
  }

  const isUser = message.role === 'user'

  return (
    <div className={`group flex gap-3 ${isUser ? 'justify-end' : ''}`}>
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
            className={`relative rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
              isUser ? 'bg-primary text-primary-foreground' : 'bg-muted'
            }`}
          >
            {isUser ? message.content : <MarkdownContent content={message.content} />}

            {/* Copy button -- assistant only */}
            {!isUser && (
              <button
                type="button"
                onClick={handleCopy}
                className="absolute -bottom-6 right-0 flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 hover:bg-accent"
                aria-label={t('copyLabel')}
              >
                {copied ? (
                  <>
                    <CheckIcon className="size-3 text-emerald-500" /> {t('copied')}
                  </>
                ) : (
                  <>
                    <CopyIcon className="size-3" /> {t('copy')}
                  </>
                )}
              </button>
            )}
          </div>
        )}

        {/* Token info -- assistant only */}
        {!isUser && tokenInfo && tokenInfo.tokens > 0 && (
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground pl-1">
            <span>
              {tokenInfo.tokens.toLocaleString()} {t('tokens')}
            </span>
            {tokenInfo.cost > 0 && <span>${tokenInfo.cost.toFixed(4)}</span>}
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
