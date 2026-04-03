'use client'

import { useState } from 'react'
import {
  Loader2Icon,
  CheckCircle2Icon,
  WrenchIcon,
  ChevronDownIcon,
  ChevronUpIcon,
  ClockIcon,
} from 'lucide-react'
import type { ToolCallInfo } from '@/lib/types'

interface ToolCallDisplayProps {
  toolCall: ToolCallInfo
  status: 'calling' | 'completed'
  result?: string
  elapsedMs?: number
}

export function ToolCallDisplay({ toolCall, status, result, elapsedMs }: ToolCallDisplayProps) {
  const [expanded, setExpanded] = useState(false)
  const hasArgs = toolCall.args && Object.keys(toolCall.args).length > 0

  return (
    <div className="rounded-lg border bg-muted/30 px-3 py-2 text-xs">
      <button
        type="button"
        className="flex w-full items-center gap-2 text-left"
        onClick={() => setExpanded(!expanded)}
      >
        {status === 'calling' ? (
          <Loader2Icon className="size-3.5 animate-spin text-primary shrink-0" />
        ) : (
          <CheckCircle2Icon className="size-3.5 text-emerald-500 shrink-0" />
        )}
        <WrenchIcon className="size-3 text-muted-foreground shrink-0" />
        <span className="font-medium">{toolCall.name}</span>
        <span className="text-muted-foreground">
          {status === 'calling' ? '호출 중...' : '완료'}
        </span>
        <span className="ml-auto" />
        {elapsedMs != null && status === 'completed' && (
          <span className="flex items-center gap-0.5 text-muted-foreground">
            <ClockIcon className="size-3" />
            {elapsedMs < 1000 ? `${elapsedMs}ms` : `${(elapsedMs / 1000).toFixed(1)}s`}
          </span>
        )}
        {(hasArgs || result) &&
          (expanded ? (
            <ChevronUpIcon className="size-3.5 text-muted-foreground shrink-0" />
          ) : (
            <ChevronDownIcon className="size-3.5 text-muted-foreground shrink-0" />
          ))}
      </button>

      {!expanded && result && (
        <div className="mt-1 text-muted-foreground line-clamp-2">{result}</div>
      )}

      {expanded && (
        <div className="mt-2 space-y-2">
          {hasArgs && (
            <div className="rounded bg-background p-2">
              <div className="mb-1 text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                파라미터
              </div>
              <pre className="whitespace-pre-wrap break-all font-mono text-[11px] text-foreground/80">
                {JSON.stringify(toolCall.args, null, 2)}
              </pre>
            </div>
          )}
          {result && (
            <div className="rounded bg-background p-2">
              <div className="mb-1 text-[10px] font-medium text-muted-foreground uppercase tracking-wider">
                결과
              </div>
              <pre className="whitespace-pre-wrap break-all font-mono text-[11px] text-foreground/80 max-h-60 overflow-auto">
                {result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
