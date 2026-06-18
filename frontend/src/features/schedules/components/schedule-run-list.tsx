'use client'

import Link from 'next/link'

import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import type { TriggerRun } from '@/lib/types'

interface ScheduleRunListLabels {
  runNow: string
  scheduled: string
  openConversation: string
  empty: string
}

interface ScheduleRunListProps {
  runs: TriggerRun[] | undefined
  loading: boolean
  labels: ScheduleRunListLabels
  formatDate: (value: string | null) => string
}

export function ScheduleRunList({ runs, loading, labels, formatDate }: ScheduleRunListProps) {
  if (loading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    )
  }

  if (!runs || runs.length === 0) {
    return <p className="text-sm text-muted-foreground">{labels.empty}</p>
  }

  return (
    <div className="space-y-2">
      {runs.map((run) => (
        <div key={run.id} className="rounded-lg border p-3 text-sm">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Badge variant={run.status === 'success' ? 'default' : 'secondary'}>
                {run.status}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {run.source === 'run_now' ? labels.runNow : labels.scheduled}
              </span>
            </div>
            <span className="text-xs text-muted-foreground">{formatDate(run.started_at)}</span>
          </div>
          <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
            {run.duration_ms !== null ? <span>{run.duration_ms}ms</span> : null}
            {run.conversation_id ? (
              <Link
                href={`/agents/${run.agent_id}/conversations/${run.conversation_id}`}
                className="text-primary-strong hover:underline"
              >
                {labels.openConversation}
              </Link>
            ) : null}
            {run.thread_id ? <span>thread {run.thread_id.slice(0, 8)}</span> : null}
          </div>
          {run.output_preview ? (
            <p className="mt-2 line-clamp-3 rounded-md bg-muted/50 p-2 text-xs text-muted-foreground">
              {run.output_preview}
            </p>
          ) : null}
          {run.error_message ? (
            <p className="mt-2 text-xs text-destructive">{run.error_message}</p>
          ) : null}
        </div>
      ))}
    </div>
  )
}
