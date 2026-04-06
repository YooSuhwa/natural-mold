'use client'

import { useState } from 'react'
import { BotIcon, UserIcon, CopyIcon, CheckIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { Message } from '@/lib/types'
import { ToolCallDisplay } from '@/components/chat/tool-call-display'
import { MarkdownContent } from '@/components/chat/markdown-content'

/**
 * Parse tool result content that may be a Python dict string or JSON.
 * Extracts the `text` field from formats like:
 *   - {'type': 'text', 'text': '...'}
 *   - [{'type': 'text', 'text': '...'}, ...]
 *   - {"type": "text", "text": "..."}
 * Falls back to the original string if parsing fails.
 */
function parseToolContent(raw: string): string {
  const trimmed = raw.trim()

  // Try JSON first (double quotes)
  try {
    const parsed = JSON.parse(trimmed)
    return extractTextFromParsed(parsed)
  } catch {
    // Not valid JSON — try Python dict (single quotes)
  }

  // Regex extraction: safer than blanket quote replacement because
  // text values may contain apostrophes (e.g. "it's sunny")
  const textMatch = trimmed.match(/['"]text['"]\s*:\s*['"]([\s\S]+?)['"]\s*[,})\]]/)
  if (textMatch) {
    return normalizeContent(textMatch[1])
  }

  // Last resort: blanket single→double quote replacement for Python dicts.
  // May break on text values containing apostrophes, but the regex above
  // should have already handled the common case.
  try {
    const jsonified = trimmed
      .replace(/'/g, '"')
      .replace(/\bTrue\b/g, 'true')
      .replace(/\bFalse\b/g, 'false')
      .replace(/\bNone\b/g, 'null')
    const parsed = JSON.parse(jsonified)
    return extractTextFromParsed(parsed)
  } catch {
    // All parsing failed — return normalized original
  }

  return normalizeContent(trimmed)
}

function extractTextFromParsed(parsed: unknown): string {
  if (Array.isArray(parsed)) {
    const texts = parsed
      .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
      .map((item) => (typeof item.text === 'string' ? item.text : ''))
      .filter(Boolean)
    if (texts.length > 0) return normalizeContent(texts.join('\n\n'))
  }

  if (typeof parsed === 'object' && parsed !== null && 'text' in parsed) {
    const text = (parsed as Record<string, unknown>).text
    if (typeof text === 'string') return normalizeContent(text)
  }

  if (typeof parsed === 'string') return normalizeContent(parsed)

  return normalizeContent(JSON.stringify(parsed, null, 2))
}

/**
 * Normalize control characters in tool result text:
 * - `\\xa0` (literal escaped) and `\xa0` (actual non-breaking space) → regular space
 * - `\\n` (literal escaped newline from Python repr) → actual newline
 * - Collapse 4+ consecutive newlines → max 3
 */
function normalizeContent(text: string): string {
  return text
    .replace(/\\xa0/g, ' ')
    .replace(/\xa0/g, ' ')
    .replace(/\\n/g, '\n')
    .replace(/\n{4,}/g, '\n\n\n')
    .trim()
}

interface MessageBubbleProps {
  message: Message
  tokenInfo?: { tokens: number; cost: number } | null
  /** Role of the previous message — used to determine spacing between messages */
  previousRole?: 'user' | 'assistant' | 'tool' | null
}

export function MessageBubble({ message, tokenInfo, previousRole }: MessageBubbleProps) {
  const [copied, setCopied] = useState(false)
  const t = useTranslations('chat.message')

  // Spacing: same role = tight, role change = wider
  const isSameRole = previousRole != null && previousRole === message.role
  // tool messages following assistant are also "same group"
  const isSameGroup = isSameRole || (message.role === 'tool' && previousRole === 'assistant')
  const gapClass = previousRole == null ? '' : isSameGroup ? 'mt-1.5' : 'mt-5'

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
    const parsedContent = parseToolContent(message.content)
    return (
      <div className={`flex gap-3 ${gapClass}`}>
        <div className="w-8" />
        <div className="max-w-[80%] rounded-lg bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
          <span className="font-medium block mb-1">{t('toolResult')}</span>
          <div className="text-foreground/80">
            <MarkdownContent content={parsedContent} className="text-xs" />
          </div>
        </div>
      </div>
    )
  }

  const isUser = message.role === 'user'

  return (
    <div className={`group flex gap-3 ${gapClass} ${isUser ? 'justify-end' : ''}`}>
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

            {/* Action bar — assistant only, appears on hover */}
            {!isUser && (
              <div className="absolute -bottom-7 right-0 flex items-center gap-0.5 rounded-lg border bg-background px-1 py-0.5 shadow-sm opacity-0 transition-opacity group-hover:opacity-100">
                <button
                  type="button"
                  onClick={handleCopy}
                  className="flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                  aria-label={t('copyLabel')}
                >
                  {copied ? (
                    <>
                      <CheckIcon className="size-3 text-emerald-500" />
                      <span className="text-emerald-500">{t('copied')}</span>
                    </>
                  ) : (
                    <>
                      <CopyIcon className="size-3" />
                      <span>{t('copy')}</span>
                    </>
                  )}
                </button>
              </div>
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
