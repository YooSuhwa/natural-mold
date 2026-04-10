'use client'

import { useState } from 'react'
import {
  ChevronDownIcon,
  WrenchIcon,
  Loader2Icon,
  CircleCheckIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { cn } from '@/lib/utils'

// ──────────────────────────────────────────────
// ToolFallbackPanel — 확장 가능 도구 패널
// ──────────────────────────────────────────────

interface ToolFallbackPanelProps {
  toolName: string
  args: Record<string, unknown>
  result?: unknown
  status: 'running' | 'complete' | 'error'
}

export function ToolFallbackPanel({ toolName, args, result, status }: ToolFallbackPanelProps) {
  const [expanded, setExpanded] = useState(false)
  const t = useTranslations('chat.toolCall')
  const hasArgs = args && Object.keys(args).length > 0
  const hasResult = result !== undefined && result !== null

  return (
    <div
      className={cn(
        'w-full rounded-xl border bg-muted/20 text-xs transition-colors',
        status === 'running' && 'border-primary/20 bg-primary/5',
        status === 'complete' && 'border-border/50',
        status === 'error' && 'border-destructive/20 bg-destructive/5',
      )}
    >
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        onClick={() => setExpanded(!expanded)}
      >
        {status === 'running' ? (
          <Loader2Icon className="size-3.5 shrink-0 animate-spin text-primary" />
        ) : (
          <CircleCheckIcon className="size-3.5 shrink-0 text-emerald-500" />
        )}
        <WrenchIcon className="size-3 shrink-0 text-muted-foreground" />
        <span className="truncate font-medium">{toolName}</span>
        <span className="shrink-0 text-muted-foreground">
          {status === 'running' ? t('calling') : t('completed')}
        </span>
        <span className="ml-auto" />
        {(hasArgs || hasResult) && (
          <ChevronDownIcon
            className={cn(
              'size-3.5 shrink-0 text-muted-foreground transition-transform duration-200',
              expanded && 'rotate-180',
            )}
          />
        )}
      </button>

      {expanded && (
        <div className="space-y-2 px-3 pb-3">
          {hasArgs && (
            <div className="rounded-lg border border-border/40 bg-background p-2.5">
              <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                {t('parameters')}
              </div>
              <pre className="whitespace-pre-wrap break-all font-mono text-[11px] text-foreground/80">
                {JSON.stringify(args, null, 2)}
              </pre>
            </div>
          )}
          {hasResult && (
            <div className="rounded-lg border border-border/40 bg-background p-2.5">
              <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                {t('results')}
              </div>
              <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] text-foreground/80">
                {typeof result === 'string' ? result : JSON.stringify(result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ──────────────────────────────────────────────
// GenericToolFallback — 미등록 도구용 폴백 UI
// ──────────────────────────────────────────────

function resolveStatus(statusType: string): 'running' | 'complete' | 'error' {
  if (statusType === 'running') return 'running'
  if (statusType === 'complete') return 'complete'
  return 'error'
}

/** 등록되지 않은 도구를 위한 폴백 UI. makeAssistantToolUI로 등록. */
export const GenericToolFallback = makeAssistantToolUI({
  toolName: '*',
  render: ({ toolName, args, result, status }) => (
    <ToolFallbackPanel
      toolName={toolName}
      args={args as Record<string, unknown>}
      result={result}
      status={resolveStatus(status.type)}
    />
  ),
})
