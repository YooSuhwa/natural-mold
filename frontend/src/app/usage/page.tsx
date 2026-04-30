'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { BarChart3Icon, DownloadIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { useDailyAggregate, useUsageSummary } from '@/lib/hooks/use-usage'
import type {
  UsageDailyEntry,
  UsageGroupBy,
  UsageMetric,
  UsageTargetKind,
} from '@/lib/types'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/shared/empty-state'
import { PageHeader } from '@/components/shared/page-header'
import { SpendLineChart } from '@/components/usage/spend-line-chart'
import { SpendBarChart } from '@/components/usage/spend-bar-chart'
import {
  formatCostUsd,
  formatRequests,
  formatTokens,
} from '@/components/usage/format'

type RangePreset = '7d' | '30d' | '90d' | 'custom'

const PRESET_DAYS: Record<Exclude<RangePreset, 'custom'>, number> = {
  '7d': 7,
  '30d': 30,
  '90d': 90,
}

function isoDaysAgo(days: number): string {
  const d = new Date()
  d.setUTCHours(0, 0, 0, 0)
  d.setUTCDate(d.getUTCDate() - days)
  return d.toISOString().slice(0, 10)
}

function isoToday(): string {
  const d = new Date()
  d.setUTCHours(0, 0, 0, 0)
  return d.toISOString().slice(0, 10)
}

function buildCsv(entries: UsageDailyEntry[]): string {
  const header = ['date', 'target_id', 'target_label', 'tokens_in', 'tokens_out', 'cost_usd', 'requests']
  const lines = [header.join(',')]
  for (const e of entries) {
    const row = [
      e.date ?? '',
      e.target_id ?? '',
      // CSV-quote labels in case they contain commas / quotes / newlines
      `"${(e.target_label ?? '').replace(/"/g, '""')}"`,
      e.total_tokens_in,
      e.total_tokens_out,
      e.total_cost_usd,
      e.request_count,
    ]
    lines.push(row.join(','))
  }
  return lines.join('\n')
}

function downloadCsv(filename: string, csv: string) {
  if (typeof document === 'undefined') return
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export default function UsagePage() {
  const t = useTranslations('usage')

  const [preset, setPreset] = useState<RangePreset>('30d')
  const [customFrom, setCustomFrom] = useState<string>(isoDaysAgo(30))
  const [customTo, setCustomTo] = useState<string>(isoToday())
  const [targetKind, setTargetKind] = useState<UsageTargetKind>('user')
  const [groupBy, setGroupBy] = useState<UsageGroupBy>('date')
  const [metric, setMetric] = useState<UsageMetric>('cost')

  const range = useMemo(() => {
    if (preset === 'custom') {
      return { from: customFrom, to: customTo }
    }
    return {
      from: isoDaysAgo(PRESET_DAYS[preset]),
      to: isoToday(),
    }
  }, [preset, customFrom, customTo])

  // Summary card uses the existing /api/usage/summary endpoint which already
  // returns "this month" totals server-side. Daily aggregate drives the chart
  // and table independently so the card numbers stay stable when filters move.
  const { data: summary } = useUsageSummary('30d')

  const dailyParams = useMemo(
    () => ({
      target_kind: targetKind,
      group_by: groupBy,
      from: range.from,
      to: range.to,
    }),
    [targetKind, groupBy, range.from, range.to],
  )

  const { data: daily, isLoading: dailyLoading } = useDailyAggregate(dailyParams)

  // Stabilise the entries reference so dependent useMemos don't re-fire when
  // TanStack Query returns the same array object.
  const entries = useMemo(() => daily ?? [], [daily])

  const monthCost = summary?.estimated_cost_usd ?? 0
  const monthTokens = summary?.total_tokens ?? 0
  const monthRequests = useMemo(
    () => entries.reduce((sum, e) => sum + e.request_count, 0),
    [entries],
  )
  const avgCostPerRequest = monthRequests > 0 ? monthCost / monthRequests : 0

  const handleCsv = () => {
    if (entries.length === 0) return
    downloadCsv(`spend-${range.from}-${range.to}.csv`, buildCsv(entries))
  }

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageHeader title={t('pageTitle')} />
      </div>

      {/* Summary cards — current month rollup, independent of filters. */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <SummaryCard label={t('summary.monthCost')} value={formatCostUsd(monthCost)} />
        <SummaryCard label={t('summary.monthTokens')} value={formatTokens(monthTokens)} />
        <SummaryCard
          label={t('summary.monthRequests')}
          value={formatRequests(monthRequests)}
        />
        <SummaryCard
          label={t('summary.avgCostPerRequest')}
          value={formatCostUsd(avgCostPerRequest)}
        />
      </div>

      {/* Filter bar */}
      <div
        className="flex flex-wrap items-end gap-3 rounded-lg border bg-card p-3"
        data-testid="usage-filter-bar"
      >
        <div className="flex flex-col gap-1.5">
          <span className="text-[11px] font-medium text-muted-foreground">
            {t('period.30d')}
          </span>
          <Select
            value={preset}
            onValueChange={(v) => v && setPreset(v as RangePreset)}
          >
            <SelectTrigger className="w-[160px]" data-testid="range-preset-select">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="7d">{t('period.7d')}</SelectItem>
              <SelectItem value="30d">{t('period.30d')}</SelectItem>
              <SelectItem value="90d">{t('period.90d')}</SelectItem>
              <SelectItem value="custom">{t('period.custom')}</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {preset === 'custom' && (
          <>
            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] font-medium text-muted-foreground">
                {t('filters.from')}
              </span>
              <Input
                type="date"
                value={customFrom}
                onChange={(e) => setCustomFrom(e.target.value)}
                className="w-[160px]"
                data-testid="custom-from"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <span className="text-[11px] font-medium text-muted-foreground">
                {t('filters.to')}
              </span>
              <Input
                type="date"
                value={customTo}
                onChange={(e) => setCustomTo(e.target.value)}
                className="w-[160px]"
                data-testid="custom-to"
              />
            </div>
          </>
        )}

        <div className="flex flex-col gap-1.5">
          <span className="text-[11px] font-medium text-muted-foreground">
            {t('filters.kind')}
          </span>
          <Tabs value={targetKind} onValueChange={(v) => setTargetKind(v as UsageTargetKind)}>
            <TabsList data-testid="target-kind-tabs">
              <TabsTrigger value="user">{t('filters.kindUser')}</TabsTrigger>
              <TabsTrigger value="agent">{t('filters.kindAgent')}</TabsTrigger>
              <TabsTrigger value="model">{t('filters.kindModel')}</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>

        <div className="flex flex-col gap-1.5">
          <span className="text-[11px] font-medium text-muted-foreground">
            {t('filters.groupBy')}
          </span>
          <Tabs value={groupBy} onValueChange={(v) => setGroupBy(v as UsageGroupBy)}>
            <TabsList data-testid="group-by-tabs">
              <TabsTrigger value="date">{t('filters.groupByDate')}</TabsTrigger>
              <TabsTrigger value="target">{t('filters.groupByTarget')}</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>

        <div className="ml-auto flex flex-col gap-1.5">
          <span className="text-[11px] font-medium text-muted-foreground">
            {t('metric.cost')}
          </span>
          <Tabs value={metric} onValueChange={(v) => setMetric(v as UsageMetric)}>
            <TabsList data-testid="metric-tabs">
              <TabsTrigger value="cost">{t('metric.cost')}</TabsTrigger>
              <TabsTrigger value="tokens">{t('metric.tokens')}</TabsTrigger>
              <TabsTrigger value="requests">{t('metric.requests')}</TabsTrigger>
            </TabsList>
          </Tabs>
        </div>
      </div>

      {/* Chart area */}
      <div className="space-y-3">
        {dailyLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : entries.length === 0 ? (
          <EmptyState
            icon={<BarChart3Icon className="size-6" />}
            title={t('empty.title')}
            description={t('empty.description')}
          />
        ) : groupBy === 'date' ? (
          <SpendLineChart data={entries} metric={metric} />
        ) : (
          <SpendBarChart data={entries} metric={metric} />
        )}
      </div>

      {/* Raw data table + CSV export */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3 pb-2">
          <CardTitle className="text-sm">{t('tableTitle')}</CardTitle>
          <Button
            variant="outline"
            size="sm"
            onClick={handleCsv}
            disabled={entries.length === 0}
            data-testid="usage-csv-download"
          >
            <DownloadIcon className="size-3.5" />
            {t('downloadCsv')}
          </Button>
        </CardHeader>
        <CardContent>
          {entries.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              {t('empty.title')}
            </p>
          ) : (
            <div className="max-h-[420px] overflow-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    {groupBy === 'date' ? (
                      <TableHead>{t('table.date')}</TableHead>
                    ) : (
                      <TableHead>{t('table.target')}</TableHead>
                    )}
                    <TableHead className="text-right">{t('table.tokensIn')}</TableHead>
                    <TableHead className="text-right">{t('table.tokensOut')}</TableHead>
                    <TableHead className="text-right">{t('table.requests')}</TableHead>
                    <TableHead className="text-right">{t('table.cost')}</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {entries.map((e, i) => (
                    <TableRow key={`${e.date ?? ''}-${e.target_id ?? ''}-${i}`}>
                      <TableCell className="font-medium">
                        {groupBy === 'date'
                          ? (e.date ?? '—')
                          : renderTargetLink(e, targetKind)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatTokens(e.total_tokens_in)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatTokens(e.total_tokens_out)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatRequests(e.request_count)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatCostUsd(e.total_cost_usd)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function SummaryCard({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-sm font-medium text-foreground/70">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <span className="text-2xl font-bold tabular-nums">{value}</span>
      </CardContent>
    </Card>
  )
}

function renderTargetLink(entry: UsageDailyEntry, kind: UsageTargetKind) {
  const label = entry.target_label ?? entry.target_id ?? '—'
  if (kind === 'agent' && entry.target_id) {
    return (
      <Link
        href={`/agents/${entry.target_id}`}
        className="text-primary hover:underline"
      >
        {label}
      </Link>
    )
  }
  return <span>{label}</span>
}
