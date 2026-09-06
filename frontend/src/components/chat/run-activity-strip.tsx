'use client'

import { useEffect, useMemo, useState } from 'react'
import { RunSummaryPanel } from '@/components/chat/run-summary'
import { cn } from '@/lib/utils'
import { buildLiveRunSummary } from '@/lib/chat/run-summary-model'
import type { RunActivity } from '@/lib/chat/langgraph-runtime/activity-model'

interface RunActivityStripProps {
  readonly activities: readonly RunActivity[]
  readonly className?: string
}

function useLiveClock(enabled: boolean): number {
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    if (!enabled) return
    const timer = window.setInterval(() => setNowMs(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [enabled])
  return nowMs
}

export function RunActivityStrip({ activities, className }: RunActivityStripProps) {
  const hasLiveActivity = activities.some(
    (activity) => activity.status === 'running' || activity.status === 'pending',
  )
  const nowMs = useLiveClock(hasLiveActivity)
  const summary = useMemo(() => buildLiveRunSummary(activities, nowMs), [activities, nowMs])
  if (!summary) return null

  return (
    <div className={cn('w-full', className)} data-testid="run-activity-strip">
      <RunSummaryPanel summary={summary} />
    </div>
  )
}
