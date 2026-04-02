'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import { SendIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface ChatInputProps {
  onSend: (content: string) => void
  disabled?: boolean
  placeholder?: string
}

export function ChatInput({ onSend, disabled, placeholder = '메시지 입력...' }: ChatInputProps) {
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isComposingRef = useRef(false)

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

  return (
    <div className="flex items-end gap-2">
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
        placeholder={placeholder}
        disabled={disabled}
        rows={1}
        className={cn(
          'min-h-[44px] max-h-[160px] w-full resize-none rounded-xl border border-input bg-transparent px-3.5 py-2.5 text-sm leading-relaxed transition-colors outline-none',
          'placeholder:text-muted-foreground',
          'focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50',
          'disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50',
          'dark:bg-input/30',
        )}
      />
      <Button
        type="button"
        size="lg"
        onClick={handleSubmit}
        disabled={disabled || !input.trim()}
        className="shrink-0"
      >
        <SendIcon className="size-4" />
        <span className="sr-only">전송</span>
      </Button>
    </div>
  )
}
