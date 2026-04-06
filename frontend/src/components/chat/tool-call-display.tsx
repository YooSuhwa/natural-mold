'use client'

import { useState, useRef, useEffect, useCallback } from 'react'
import {
  Loader2Icon,
  CircleCheckIcon,
  WrenchIcon,
  ChevronDownIcon,
  ClockIcon,
  CopyIcon,
  CheckIcon,
} from 'lucide-react'
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
    requestAnimationFrame(() => {
      if (contentRef.current) {
        setContentHeight(contentRef.current.scrollHeight)
      }
    })
  }, [expanded, result])

  return (
    <div
      className={cn(
        'w-full rounded-xl border bg-muted/20 text-xs transition-colors',
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

      {/* Expandable detail */}
      <div
        className="overflow-hidden transition-[max-height] duration-200 ease-in-out"
        style={{ maxHeight: expanded ? `${contentHeight}px` : '0px' }}
      >
        <div ref={contentRef} className="space-y-2 px-3 pb-3">
          {hasArgs && (
            <DetailSection label={t('parameters')}>
              <pre className="whitespace-pre-wrap break-all font-mono text-[11px] text-foreground/80">
                {JSON.stringify(toolCall.args, null, 2)}
              </pre>
            </DetailSection>
          )}
          {result && <ResultSection label={t('results')} content={result} />}
        </div>
      </div>
    </div>
  )
}

function DetailSection({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border/40 bg-background p-2.5">
      <div className="mb-1.5 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
        {label}
      </div>
      {children}
    </div>
  )
}

function ResultSection({ label, content }: { label: string; content: string }) {
  const [mode, setMode] = useState<'pretty' | 'raw'>('pretty')
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // clipboard API may not be available
    }
  }, [content])

  const prettyContent = mode === 'pretty' ? tryPrettyFormat(content) : content

  return (
    <div className="rounded-lg border border-border/40 bg-background p-2.5">
      <div className="mb-1.5 flex items-center justify-between">
        <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
          {label}
        </span>
        <div className="flex items-center gap-1.5">
          {/* Pretty / Raw toggle */}
          <div className="flex gap-0.5">
            <button
              type="button"
              onClick={() => setMode('pretty')}
              className={cn(
                'rounded px-1.5 py-0.5 text-[10px] transition-colors',
                mode === 'pretty'
                  ? 'bg-muted text-foreground font-medium'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              Pretty
            </button>
            <button
              type="button"
              onClick={() => setMode('raw')}
              className={cn(
                'rounded px-1.5 py-0.5 text-[10px] transition-colors',
                mode === 'raw'
                  ? 'bg-muted text-foreground font-medium'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              Raw
            </button>
          </div>
          {/* Copy button */}
          <button
            type="button"
            onClick={handleCopy}
            className="rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
            aria-label="Copy result"
          >
            {copied ? (
              <CheckIcon className="size-3 text-emerald-500" />
            ) : (
              <CopyIcon className="size-3" />
            )}
          </button>
        </div>
      </div>
      <pre className="whitespace-pre-wrap break-all font-mono text-[11px] text-foreground/80 max-h-60 overflow-auto">
        {prettyContent}
      </pre>
    </div>
  )
}

/** Try to pretty-format content as JSON; return original if not valid JSON */
function tryPrettyFormat(text: string): string {
  try {
    const parsed = JSON.parse(text)
    return JSON.stringify(parsed, null, 2)
  } catch {
    return text
  }
}
