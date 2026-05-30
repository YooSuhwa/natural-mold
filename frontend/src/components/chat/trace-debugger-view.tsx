'use client'

import { useMemo } from 'react'
import { useQueries } from '@tanstack/react-query'
import { ActivityIcon, ArrowLeftIcon, ExternalLinkIcon, RefreshCwIcon } from 'lucide-react'

import { conversationsApi } from '@/lib/api/conversations'
import { toAgentPrismTraceViewerData } from '@/lib/agent-prism/trace-adapter'
import type { DebugTraceDetailResponse } from '@/lib/types'
import {
  conversationKeys,
  useConversationDebugTraces,
} from '@/lib/hooks/use-conversations'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { TraceViewer } from '@/components/agent-prism/TraceViewer/TraceViewer'

interface TraceDebuggerViewProps {
  conversationId: string
  backHref?: string
}

export function TraceDebuggerView({ conversationId, backHref }: TraceDebuggerViewProps) {
  const tracesQuery = useConversationDebugTraces(conversationId, true)
  const traces = useMemo(() => tracesQuery.data?.traces ?? [], [tracesQuery.data?.traces])

  const detailQueries = useQueries({
    queries: traces.map((trace) => ({
      queryKey: conversationKeys.debugTraceDetail(conversationId, trace.trace_id),
      queryFn: () => conversationsApi.debugTraceDetail(conversationId, trace.trace_id),
      enabled: Boolean(conversationId && trace.trace_id),
      staleTime: 10_000,
    })),
  })

  const details = detailQueries.map((query) => query.data) as Array<
    DebugTraceDetailResponse | undefined
  >
  const viewerData = useMemo(() => toAgentPrismTraceViewerData(traces, details), [traces, details])
  const viewerKey = viewerData
    .map((item) => `${item.traceRecord.id}:${item.traceRecord.spansCount}`)
    .join('|')
  const loading = tracesQuery.isLoading || detailQueries.some((query) => query.isLoading)
  const fallbackReason =
    tracesQuery.data?.fallback_reason ??
    details.find((detail) => detail?.fallback_reason)?.fallback_reason ??
    null
  const hasFallback =
    traces.some((trace) => trace.fallback) || details.some((detail) => detail?.trace.fallback)
  const firstLangfuseUrl = traces.find((trace) => trace.langfuse_url)?.langfuse_url

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-hidden bg-slate-50/80 p-3 dark:bg-slate-950/40">
      <section className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border bg-background shadow-sm">
        <header className="shrink-0 border-b px-5 py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h1 className="flex items-center gap-2 font-heading text-lg font-semibold">
                <ActivityIcon className="size-4" />
                Trace debugger
              </h1>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="font-mono">{conversationId}</span>
                <Badge variant="outline">AgentPrism</Badge>
                {fallbackReason ? <Badge variant="outline">{fallbackReason}</Badge> : null}
                {hasFallback ? <Badge variant="outline">message_events fallback</Badge> : null}
              </div>
            </div>
            <div className="flex items-center gap-2">
              {firstLangfuseUrl ? (
                <Button
                  variant="outline"
                  size="sm"
                  render={<a href={firstLangfuseUrl} target="_blank" rel="noreferrer" />}
                >
                  <ExternalLinkIcon className="size-3.5" />
                  Langfuse
                </Button>
              ) : null}
              {backHref ? (
                <Button variant="outline" size="sm" render={<a href={backHref} />}>
                  <ArrowLeftIcon className="size-3.5" />
                  Chat
                </Button>
              ) : null}
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  void tracesQuery.refetch()
                  detailQueries.forEach((query) => void query.refetch())
                }}
              >
                <RefreshCwIcon className="size-3.5" />
                Refresh
              </Button>
            </div>
          </div>
        </header>

        <div className="min-h-0 flex-1 overflow-hidden bg-agentprism-background">
          {loading ? (
            <div className="grid h-full grid-cols-[240px_1fr_360px] gap-px bg-border">
              <TraceSkeleton />
              <TraceSkeleton />
              <TraceSkeleton />
            </div>
          ) : viewerData.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
              No trace rows
            </div>
          ) : (
            <TraceViewer key={viewerKey} data={viewerData} />
          )}
        </div>
      </section>
    </div>
  )
}

function TraceSkeleton() {
  return (
    <div className="space-y-3 bg-background p-4">
      <Skeleton className="h-5 w-32" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-20 w-full" />
    </div>
  )
}
