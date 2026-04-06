'use client'

import { useState, useRef, useEffect } from 'react'
import { Loader2Icon, CircleCheckIcon, WrenchIcon, ChevronDownIcon, ClockIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import type { ToolCallInfo } from '@/lib/types'

interface ToolCallDisplayProps {
  toolCall: ToolCallInfo
  status: 'calling' | 'completed'
  result?: string
  elapsedMs?: number
}

export function ToolCallDisplay({ toolCall, status, result, elapsedMs }: ToolCallDisplayProps) {
  const [expanded, setExpanded] = useState(false)
  const contentRef = useRef<HTMLDivElement>(null)
  const [contentHeight, setContentHeight] = useState(0)
  const t = useTranslations('chat.toolCall')
  const hasArgs = toolCall.args && Object.keys(toolCall.args).length > 0

  useEffect(() => {
    if (contentRef.current) {
      setContentHeight(contentRef.current.scrollHeight)
    }
  }, [expanded, result])

  return (
    <div
      className={cn(
        'rounded-xl border bg-muted/20 text-xs transition-colors',
        status === 'calling' && 'border-primary/20 bg-primary/5',
        status === 'completed' && 'border-border/50',
      )}
    >
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        onClick={() => setExpanded(!expanded)}
      >
        {status === 'calling' ? (
          <Loader2Icon className="size-3.5 animate-spin text-primary shrink-0" />
        ) : (
          <CircleCheckIcon className="size-3.5 text-emerald-500 shrink-0" />
        )}
        <WrenchIcon className="size-3 text-muted-foreground shrink-0" />
        <span className="font-medium truncate">{toolCall.name}</span>
        <span className="text-muted-foreground shrink-0">
          {status === 'calling' ? t('calling') : t('completed')}
        </span>
        <span className="ml-auto" />
        {elapsedMs != null && status === 'completed' && (
          <span className="flex items-center gap-0.5 text-muted-foreground shrink-0">
            <ClockIcon className="size-3" />
            {elapsedMs < 1000 ? `${elapsedMs}ms` : `${(elapsedMs / 1000).toFixed(1)}s`}
          </span>
        )}
        {(hasArgs || result) && (
          <ChevronDownIcon
            className={cn(
              'size-3.5 text-muted-foreground shrink-0 transition-transform duration-200',
              expanded && 'rotate-180',
            )}
          />
        )}
      </button>

      {/* Preview when collapsed */}
      {!expanded && result && (
        <div className="px-3 pb-2 text-muted-foreground line-clamp-2">{result}</div>
      )}

      {/* Expandable detail */}
      <div
        className="overflow-hidden transition-[max-height] duration-200 ease-in-out"
        style={{ maxHeight: expanded ? `${contentHeight}px` : '0px' }}
      >
        <div ref={contentRef} className="space-y-2 px-3 pb-3">
          {hasArgs && (
            <div className="rounded-lg bg-background/80 p-2.5">
              <div className="mb-1.5 text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                {t('parameters')}
              </div>
              <pre className="whitespace-pre-wrap break-all font-mono text-[11px] text-foreground/80">
                {JSON.stringify(toolCall.args, null, 2)}
              </pre>
            </div>
          )}
          {result && (
            <div className="rounded-lg bg-background/80 p-2.5">
              <div className="mb-1.5 text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                {t('results')}
              </div>
              <pre className="whitespace-pre-wrap break-all font-mono text-[11px] text-foreground/80 max-h-60 overflow-auto">
                {result}
              </pre>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
