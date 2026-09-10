import { QuoteIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import type { QuotedResource } from '@/lib/chat/context/quoted-resource-message'

export function QuotedResourceMessage({ quotes }: { readonly quotes: readonly QuotedResource[] }) {
  const t = useTranslations('chat.sideChat')
  return (
    <details
      className="ml-auto max-w-lg rounded-lg border border-border/60 bg-background px-3 py-2 text-sm"
      data-testid="sent-quote-context"
    >
      <summary className="flex cursor-pointer items-center gap-2 text-muted-foreground">
        <QuoteIcon className="size-3.5" />
        {t('quotedContext')} · {quotes.length}
      </summary>
      <div className="mt-3 space-y-3">
        {quotes.map((quote, index) => (
          <div key={`${quote.id}:${quote.message_id}:${index}`} className="space-y-1.5">
            <p className="text-xs text-muted-foreground">{quote.label}</p>
            <blockquote className="max-h-44 overflow-y-auto whitespace-pre-wrap break-words border-l-2 border-border pl-3">
              {quote.quote ?? quote.text}
            </blockquote>
            {quote.comment ? (
              <p className="whitespace-pre-wrap break-words text-xs">
                {t('comment')}: {quote.comment}
              </p>
            ) : null}
          </div>
        ))}
      </div>
    </details>
  )
}
