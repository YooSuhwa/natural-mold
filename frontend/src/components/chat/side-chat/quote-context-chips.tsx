'use client'

import { useId, useState } from 'react'
import { MessageSquareTextIcon, ArrowUpRightIcon, XIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from '@/components/ui/popover'
import {
  resourceContextReferenceKey,
  type ResourceContextReference,
} from '@/lib/chat/context/resource-context'
import { useSideChat, type MessageQuote } from './side-chat-context'

export function isMessageQuote(reference: ResourceContextReference): reference is MessageQuote {
  return reference.kind === 'conversation' && Boolean(reference.message_id && reference.quote)
}

export function QuoteContextChips({
  references,
  onRemove,
  onUpdate,
}: {
  readonly references: readonly ResourceContextReference[]
  readonly onRemove: (reference: ResourceContextReference) => void
  readonly onUpdate: (reference: ResourceContextReference, next: ResourceContextReference) => void
}) {
  const quotes = references.filter(isMessageQuote)
  if (!quotes.length) return null
  return (
    <div className="flex flex-wrap gap-2 px-3 pt-3 pb-1" data-testid="quote-context-chips">
      {quotes.map((quote, index) => (
        <QuoteChip
          key={resourceContextReferenceKey(quote)}
          quote={quote}
          number={index + 1}
          onRemove={() => onRemove(quote)}
          onSave={(comment) => onUpdate(quote, { ...quote, comment })}
        />
      ))}
    </div>
  )
}

function QuoteChip({
  quote,
  number,
  onRemove,
  onSave,
}: {
  readonly quote: MessageQuote
  readonly number: number
  readonly onRemove: () => void
  readonly onSave: (comment: string) => void
}) {
  const t = useTranslations('chat.sideChat')
  const workspace = useSideChat()
  const [open, setOpen] = useState(false)
  const previewId = useId()
  const [comment, setComment] = useState(quote.comment ?? '')
  const source = quote.label ?? t('sourceConversation')
  const jumpToSource = () => {
    if (quote.id === workspace?.sideId) workspace.show()
    else if (workspace?.open && window.matchMedia('(max-width: 1279px)').matches) workspace.close()
    requestAnimationFrame(() => {
      const threads = document.querySelectorAll<HTMLElement>('[data-chat-selection-thread]')
      const thread = Array.from(threads).find(
        (item) => item.dataset.chatSelectionThread === quote.id,
      )
      const message = Array.from(
        thread?.querySelectorAll<HTMLElement>('[data-moldy-message-id]') ?? [],
      ).find((item) => item.dataset.moldyMessageId === quote.message_id)
      message?.scrollIntoView({
        block: 'center',
        behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches
          ? 'instant'
          : 'smooth',
      })
      message?.focus({ preventScroll: true })
    })
    setOpen(false)
  }
  return (
    <Popover
      open={open}
      onOpenChange={(nextOpen) => {
        if (nextOpen) setComment(quote.comment ?? '')
        setOpen(nextOpen)
      }}
    >
      <div className="inline-flex min-w-0 items-center rounded-lg border border-border bg-background text-xs">
        <Tooltip disabled={open}>
          <TooltipTrigger
            aria-describedby={!open ? previewId : undefined}
            render={
              <PopoverTrigger className="inline-flex min-w-0 items-center gap-1.5 rounded-lg px-2.5 py-2 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground" />
            }
          >
            <MessageSquareTextIcon className="size-3.5" />
            <span>{t('quoteNumber', { number })}</span>
          </TooltipTrigger>
          <TooltipContent
            id={previewId}
            role="tooltip"
            className="moldy-popover block w-80 bg-popover text-popover-foreground p-3"
            side="top"
            align="start"
          >
            <QuotePreview quote={quote} source={source} />
          </TooltipContent>
        </Tooltip>
        <button
          type="button"
          onClick={onRemove}
          className="rounded-md p-2 text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label={t('removeQuote', { number })}
        >
          <XIcon className="size-3.5" />
        </button>
      </div>
      <PopoverContent className="w-80 p-4" finalFocus={false}>
        <PopoverTitle className="mb-3 text-sm font-medium">
          {t('quoteNumber', { number })}
        </PopoverTitle>
        <QuotePreview quote={quote} source={source} />
        <label className="mt-3 block text-xs text-muted-foreground">
          {t('comment')}
          <Textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            maxLength={2000}
            className="mt-1.5 min-h-20 text-sm"
            placeholder={t('commentPlaceholder')}
          />
        </label>
        <div className="mt-3 flex items-center justify-between gap-2">
          <Button variant="ghost" size="sm" onClick={jumpToSource}>
            <ArrowUpRightIcon className="size-3.5" />
            {t('viewSource')}
          </Button>
          <Button
            size="sm"
            onClick={() => {
              onSave(comment)
              setOpen(false)
            }}
          >
            {t('saveComment')}
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}

function QuotePreview({
  quote,
  source,
}: {
  readonly quote: MessageQuote
  readonly source: string
}) {
  const t = useTranslations('chat.sideChat')
  return (
    <div className="space-y-2 text-left text-sm">
      <p className="truncate text-xs text-muted-foreground">{source}</p>
      <p className="text-xs text-muted-foreground">
        {t(
          quote.message_role === 'user'
            ? 'userMessage'
            : quote.message_role === 'assistant'
              ? 'assistantMessage'
              : 'sourceMessage',
        )}
      </p>
      <blockquote className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words border-l-2 border-border pl-3 text-foreground">
        {quote.quote}
      </blockquote>
      {quote.comment ? (
        <p className="break-words text-xs">
          <span className="text-muted-foreground">{t('comment')}: </span>
          {quote.comment}
        </p>
      ) : null}
    </div>
  )
}
