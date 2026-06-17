'use client'

import { useMemo, useState } from 'react'
import { useQueries } from '@tanstack/react-query'
import { filterSpansRecursively, flattenSpans } from '@evilmartians/agent-prism-data'
import type { TraceSpan } from '@evilmartians/agent-prism-types'
import { useTranslations } from 'next-intl'
import {
  ArrowLeftIcon,
  CheckCircle2Icon,
  ExternalLinkIcon,
  FilterIcon,
  RefreshCwIcon,
} from 'lucide-react'

import { conversationsApi } from '@/lib/api/conversations'
import { toAgentPrismTraceViewerData } from '@/lib/agent-prism/trace-adapter'
import type { DebugTraceDetailResponse, DebugTraceSummary } from '@/lib/types'
import { conversationKeys, useConversationDebugTraces } from '@/lib/hooks/use-conversations'
import { formatDisplayDateTime, formatDisplayNumber } from '@/lib/utils/display-format'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { TraceViewerData } from '@/components/agent-prism/TraceViewer/TraceViewer'
import { DetailsView } from '@/components/agent-prism/DetailsView/DetailsView'
import { TraceViewerPlaceholder } from '@/components/agent-prism/TraceViewer/TraceViewerPlaceholder'
import { TraceViewerTreeViewContainer } from '@/components/agent-prism/TraceViewer/TraceViewerTreeViewContainer'

interface TraceDebuggerViewProps {
  conversationId: string
  backHref?: string
}

export function TraceDebuggerView({ conversationId, backHref }: TraceDebuggerViewProps) {
  const t = useTranslations('chat.traceDebugger')
  const tracesQuery = useConversationDebugTraces(conversationId, true)
  const [selectedSpanId, setSelectedSpanId] = useState<string | undefined>()
  const [searchValue, setSearchValue] = useState('')
  const [expandedSpansOverride, setExpandedSpansOverride] = useState<string[] | null>(null)
  const traces = useMemo(
    () =>
      [...(tracesQuery.data?.traces ?? [])].sort(
        (a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime(),
      ),
    [tracesQuery.data?.traces],
  )

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
  const traceContentSpans = useMemo(
    () => viewerData.flatMap((traceData) => traceData.spans),
    [viewerData],
  )
  const rootSpans = useMemo(
    () => buildConversationTraceTree(conversationId, viewerData, t('conversationTraces')),
    [conversationId, viewerData, t],
  )
  const allSpans = useMemo(() => flattenSpans(rootSpans), [rootSpans])
  const selectedSpan = useMemo(
    () => allSpans.find((span) => span.id === selectedSpanId) ?? allSpans[0],
    [allSpans, selectedSpanId],
  )
  const setSelectedSpan = (span: TraceSpan | undefined) => setSelectedSpanId(span?.id)
  const filteredSpans = useMemo(() => {
    if (!searchValue.trim()) return rootSpans
    return filterSpansRecursively(rootSpans, searchValue)
  }, [rootSpans, searchValue])
  const allIds = useMemo(() => allSpans.map((span) => span.id), [allSpans])
  const expandedSpansIds = expandedSpansOverride ?? allIds
  const loading = tracesQuery.isLoading || detailQueries.some((query) => query.isLoading)
  const firstLangfuseUrl = traces.find((trace) => trace.langfuse_url)?.langfuse_url

  const handleExpandAll = () => setExpandedSpansOverride(null)
  const handleCollapseAll = () => setExpandedSpansOverride([])

  return (
    <div className="moldy-app-surface flex min-h-0 flex-1 flex-col overflow-hidden p-3">
      <section className="moldy-panel flex min-h-0 flex-1 flex-col overflow-hidden">
        <header className="moldy-panel-header shrink-0 px-5 py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <h1 className="flex min-w-0 items-center gap-2 font-heading text-lg font-semibold">
                <span className="truncate">{t('title')}</span>
                <span className="moldy-resource-meta max-w-none font-mono">{conversationId}</span>
              </h1>
            </div>
            <div className="flex items-center gap-2">
              {firstLangfuseUrl ? (
                <Button
                  variant="outline"
                  size="sm"
                  render={<a href={firstLangfuseUrl} target="_blank" rel="noopener noreferrer" />}
                >
                  <ExternalLinkIcon className="size-3.5" />
                  {t('openLogs')}
                </Button>
              ) : null}
              {backHref ? (
                <Button variant="outline" size="sm" render={<a href={backHref} />}>
                  <ArrowLeftIcon className="size-3.5" />
                  {t('backToList')}
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
                {t('refresh')}
              </Button>
            </div>
          </div>
        </header>

        <div className="moldy-trace-grid grid min-h-0 flex-1 grid-cols-[280px_minmax(420px,1fr)_minmax(360px,42%)] gap-px overflow-hidden">
          {loading ? (
            <>
              <TraceSkeleton />
              <TraceSkeleton />
              <TraceSkeleton />
            </>
          ) : viewerData.length === 0 ? (
            <div className="moldy-trace-panel col-span-3 flex h-full items-center justify-center text-sm text-muted-foreground">
              {t('emptyRows')}
            </div>
          ) : (
            <TraceRunLayout
              traceCount={viewerData.length}
              traces={traces}
              metricSpans={flattenSpans(traceContentSpans)}
              filteredSpans={filteredSpans}
              searchValue={searchValue}
              setSearchValue={setSearchValue}
              selectedSpan={selectedSpan}
              setSelectedSpan={setSelectedSpan}
              expandedSpansIds={expandedSpansIds}
              setExpandedSpansIds={setExpandedSpansOverride}
              handleExpandAll={handleExpandAll}
              handleCollapseAll={handleCollapseAll}
            />
          )}
        </div>
      </section>
    </div>
  )
}

interface TraceRunLayoutProps {
  traceCount: number
  traces: DebugTraceSummary[]
  metricSpans: TraceSpan[]
  filteredSpans: TraceSpan[]
  searchValue: string
  setSearchValue: (value: string) => void
  selectedSpan: TraceSpan | undefined
  setSelectedSpan: (span: TraceSpan | undefined) => void
  expandedSpansIds: string[]
  setExpandedSpansIds: (ids: string[]) => void
  handleExpandAll: () => void
  handleCollapseAll: () => void
}

function TraceRunLayout({
  traceCount,
  traces,
  metricSpans,
  filteredSpans,
  searchValue,
  setSearchValue,
  selectedSpan,
  setSelectedSpan,
  expandedSpansIds,
  setExpandedSpansIds,
  handleExpandAll,
  handleCollapseAll,
}: TraceRunLayoutProps) {
  const t = useTranslations('chat.traceDebugger')

  return (
    <>
      <RunInfoPanel traces={traces} spans={metricSpans} />
      <main className="moldy-trace-panel flex min-h-0 flex-col px-5 py-4">
        <div className="mb-3 flex shrink-0 items-center justify-between gap-3">
          <h2 className="font-heading text-lg font-semibold">{t('spanDetails')}</h2>
          <span className="text-xs text-muted-foreground">
            {t('spanSummary', { traceCount, spanCount: metricSpans.length })}
          </span>
        </div>
        <div className="moldy-trace-tree min-h-0 flex-1 overflow-hidden p-3">
          <TraceViewerTreeViewContainer
            searchValue={searchValue}
            setSearchValue={setSearchValue}
            handleExpandAll={handleExpandAll}
            handleCollapseAll={handleCollapseAll}
            filteredSpans={filteredSpans}
            selectedSpan={selectedSpan}
            setSelectedSpan={setSelectedSpan}
            expandedSpansIds={expandedSpansIds}
            setExpandedSpansIds={setExpandedSpansIds}
            spanCardViewOptions={{ withStatus: true, expandButton: 'inside' }}
            showHeader={false}
          />
        </div>
      </main>
      <aside className="moldy-trace-rail min-h-0 p-4">
        {selectedSpan ? (
          <DetailsView data={selectedSpan} className="moldy-card bg-background" />
        ) : (
          <TraceViewerPlaceholder title={t('selectSpanPlaceholder')} />
        )}
      </aside>
    </>
  )
}

function buildConversationTraceTree(
  conversationId: string,
  traces: TraceViewerData[],
  title: string,
): TraceSpan[] {
  if (traces.length === 0) return []
  const childSpans = traces.flatMap((traceData) => traceData.spans)
  if (childSpans.length === 0) return []

  const start = minTime(childSpans.map((span) => span.startTime.getTime())) ?? Date.now()
  const end = maxTime(childSpans.map((span) => span.endTime.getTime())) ?? start
  return [
    {
      id: `conversation:${conversationId}:traces`,
      title,
      startTime: new Date(start),
      endTime: new Date(end),
      duration: Math.max(0, end - start),
      type: 'agent_invocation',
      raw: JSON.stringify(
        {
          conversation_id: conversationId,
          trace_count: traces.length,
        },
        null,
        2,
      ),
      status: childSpans.some((span) => span.status === 'error') ? 'error' : 'success',
      children: childSpans,
    },
  ]
}

function minTime(values: number[]): number | null {
  const valid = values.filter((value) => Number.isFinite(value))
  return valid.length ? Math.min(...valid) : null
}

function maxTime(values: number[]): number | null {
  const valid = values.filter((value) => Number.isFinite(value))
  return valid.length ? Math.max(...valid) : null
}

function RunInfoPanel({ traces, spans }: { traces: DebugTraceSummary[]; spans: TraceSpan[] }) {
  const t = useTranslations('chat.traceDebugger')
  const errorCount = spans.filter((span) => span.status === 'error').length
  const toolCount = spans.filter((span) => span.type === 'tool_execution').length
  const llmCount = spans.filter((span) => span.type === 'llm_call').length
  const failed = traces.some((trace) => trace.status === 'failed')
  const startedAt = earliestDate(traces.map((trace) => trace.started_at))
  const completedAt = latestDate(traces.map((trace) => trace.completed_at))
  const totalDuration = traces.reduce((sum, trace) => sum + (trace.duration_ms ?? 0), 0)
  const totalTokens = traces.reduce((sum, trace) => sum + (trace.total_tokens ?? 0), 0)

  return (
    <aside className="moldy-trace-rail flex min-h-0 flex-col overflow-y-auto px-5 py-4">
      <section className="moldy-card p-4">
        <div className="mb-4 flex items-center justify-between gap-2">
          <h2 className="font-heading text-base font-semibold">{t('runInfo')}</h2>
          <Badge variant={failed ? 'destructive' : 'outline'}>
            {failed ? t('statusFailed') : t('statusSuccess')}
          </Badge>
        </div>
        <div className="space-y-4 text-sm">
          <InfoRow label={t('traceCount')} value={formatDisplayNumber(traces.length)} />
          <InfoRow label={t('startedAt')} value={formatTraceDateTime(startedAt)} />
          <InfoRow label={t('completedAt')} value={formatTraceDateTime(completedAt)} />
          <InfoRow label={t('totalDuration')} value={formatDuration(totalDuration || null)} />
          <InfoRow label={t('totalTokens')} value={formatDisplayNumber(totalTokens || null)} />
        </div>
      </section>

      <section className="mt-5 space-y-3">
        <h2 className="font-heading text-base font-semibold">{t('filters')}</h2>
        <div className="moldy-card p-4">
          <div className="mb-4 flex items-center gap-2 text-sm">
            <FilterIcon className="size-4 text-muted-foreground" />
            <span>{t('total')}</span>
            <Badge variant="secondary">{spans.length}</Badge>
          </div>
          <div className="grid grid-cols-3 gap-3 text-center">
            <Metric label={t('llm')} value={llmCount} />
            <Metric label={t('tools')} value={toolCount} />
            <Metric label={t('errors')} value={errorCount} />
          </div>
        </div>

        <FilterEmptyState title={t('privacy')} />
        <FilterEmptyState title={t('secrets')} />
      </section>
    </aside>
  )
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 font-medium">{value}</div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-xl font-semibold">{value}</div>
    </div>
  )
}

function FilterEmptyState({ title }: { title: string }) {
  const t = useTranslations('chat.traceDebugger')

  return (
    <div className="moldy-card p-4">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium">
        <CheckCircle2Icon className="size-4 text-muted-foreground" />
        {title}
      </div>
      <div className="rounded-md border border-dashed px-3 py-5 text-center text-xs text-muted-foreground">
        {t('emptyFilterItems')}
      </div>
    </div>
  )
}

function earliestDate(values: Array<string | null | undefined>): string | null {
  return dateBoundary(values, (left, right) => left < right)
}

function latestDate(values: Array<string | null | undefined>): string | null {
  return dateBoundary(values, (left, right) => left > right)
}

function dateBoundary(
  values: Array<string | null | undefined>,
  compare: (left: number, right: number) => boolean,
): string | null {
  let selected: { value: string; time: number } | null = null
  for (const value of values) {
    if (!value) continue
    const time = new Date(value).getTime()
    if (Number.isNaN(time)) continue
    if (!selected || compare(time, selected.time)) {
      selected = { value, time }
    }
  }
  return selected?.value ?? null
}

function formatTraceDateTime(value: string | null | undefined): string {
  return formatDisplayDateTime(value, {
    format: {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    },
  })
}

function formatDuration(value: number | null | undefined): string {
  if (value == null) return '-'
  if (value < 1000) return `${value}ms`
  return `${(value / 1000).toFixed(2)}s`
}

function TraceSkeleton() {
  return (
    <div className="moldy-trace-panel space-y-3 p-4">
      <Skeleton className="h-5 w-32" />
      <Skeleton className="h-10 w-full" />
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-20 w-full" />
    </div>
  )
}
