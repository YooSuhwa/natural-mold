'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { SendIcon, PaperclipIcon, ArrowDownToLineIcon, ArrowUpFromLineIcon } from 'lucide-react'
import { useAtomValue } from 'jotai'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { ComingSoonButton } from '@/components/shared/coming-soon-button'
import { cn } from '@/lib/utils'
import { sessionTokenUsageAtom } from '@/lib/stores/chat-store'

interface ChatInputProps {
  onSend: (content: string) => void
  disabled?: boolean
  placeholder?: string
  modelName?: string
}

export function ChatInput({ onSend, disabled, placeholder, modelName }: ChatInputProps) {
  const t = useTranslations('chat.input')
  const tc = useTranslations('common')
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isComposingRef = useRef(false)
  const tokenUsage = useAtomValue(sessionTokenUsageAtom)

  const adjustHeight = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = '0'
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`
  }, [])

  useEffect(() => {
    adjustHeight()
  }, [input, adjustHeight])

  function handleSubmit() {
    if (!input.trim() || disabled) return
    onSend(input.trim())
    setInput('')
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !isComposingRef.current) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const hasTokens = tokenUsage.inputTokens > 0 || tokenUsage.outputTokens > 0

  return (
    <div className={cn('overflow-hidden rounded-2xl border border-input bg-background shadow-sm')}>
      {/* Model & Token bar */}
      {(modelName || hasTokens) && (
        <div className="flex items-center gap-3 border-b border-input/50 px-3.5 py-1.5 text-xs text-muted-foreground">
          {modelName && <span className="font-medium text-foreground/70">{modelName}</span>}
          {hasTokens && (
            <>
              {modelName && <span className="text-border">·</span>}
              <span className="flex items-center gap-1">
                <ArrowDownToLineIcon className="size-3" />
                {formatTokens(tokenUsage.inputTokens)}
              </span>
              <span className="flex items-center gap-1">
                <ArrowUpFromLineIcon className="size-3" />
                {formatTokens(tokenUsage.outputTokens)}
              </span>
              {tokenUsage.cost > 0 && (
                <>
                  <span className="text-border">·</span>
                  <span>{formatCost(tokenUsage.cost)}</span>
                </>
              )}
            </>
          )}
        </div>
      )}

      {/* Textarea */}
      <textarea
        ref={textareaRef}
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        onCompositionStart={() => {
          isComposingRef.current = true
        }}
        onCompositionEnd={() => {
          isComposingRef.current = false
        }}
        placeholder={placeholder ?? t('placeholder')}
        disabled={disabled}
        rows={1}
        className={cn(
          'min-h-[44px] max-h-[160px] w-full resize-none bg-transparent px-3.5 py-2.5 text-sm leading-relaxed outline-none',
          'placeholder:text-muted-foreground',
          'disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50',
        )}
      />

      {/* Toolbar */}
      <div className="flex items-center justify-between px-2 py-1.5">
        <div className="flex items-center gap-1">
          <ComingSoonButton message={tc('comingSoon.fileAttach')} className="text-muted-foreground">
            <PaperclipIcon className="size-4" />
            <span className="sr-only">{tc('comingSoon.fileAttach')}</span>
          </ComingSoonButton>
        </div>
        <Button
          type="button"
          size="icon-sm"
          onClick={handleSubmit}
          disabled={disabled || !input.trim()}
          className={cn(
            'rounded-full transition-all',
            input.trim() && !disabled
              ? 'bg-primary text-primary-foreground shadow-sm'
              : 'bg-muted text-muted-foreground',
          )}
        >
          <SendIcon className="size-4" />
          <span className="sr-only">{t('sendButton')}</span>
        </Button>
      </div>
    </div>
  )
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function formatCost(n: number): string {
  if (n < 0.01) return `$${n.toFixed(4)}`
  return `$${n.toFixed(2)}`
}
