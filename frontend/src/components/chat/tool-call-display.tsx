'use client'

import { Loader2Icon, CheckCircle2Icon, WrenchIcon } from 'lucide-react'
import type { ToolCallInfo } from '@/lib/types'

interface ToolCallDisplayProps {
  toolCall: ToolCallInfo
  status: 'calling' | 'completed'
  result?: string
}

export function ToolCallDisplay({ toolCall, status, result }: ToolCallDisplayProps) {
  return (
    <div className="rounded-lg border bg-muted/30 px-3 py-2 text-xs">
      <div className="flex items-center gap-2">
        {status === 'calling' ? (
          <Loader2Icon className="size-3.5 animate-spin text-primary" />
        ) : (
          <CheckCircle2Icon className="size-3.5 text-emerald-500" />
        )}
        <WrenchIcon className="size-3 text-muted-foreground" />
        <span className="font-medium">{toolCall.name}</span>
        <span className="text-muted-foreground">
          {status === 'calling' ? '호출 중...' : '완료'}
        </span>
      </div>
      {result && <div className="mt-1 text-muted-foreground line-clamp-2">{result}</div>}
    </div>
  )
}
