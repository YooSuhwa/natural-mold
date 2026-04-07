'use client'

import { useState } from 'react'
import Link from 'next/link'
import { BarChart3Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useUsageSummary } from '@/lib/hooks/use-usage'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select'
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

const PERIODS = ['7d', '30d', '90d', 'all'] as const

export default function UsagePage() {
  const [period, setPeriod] = useState<(typeof PERIODS)[number]>('30d')
  const { data: usage, isLoading } = useUsageSummary(period === 'all' ? undefined : period)
  const t = useTranslations('usage')

  const periodSelector = (
    <Select
      value={period}
      onValueChange={(val) => {
        if (val) setPeriod(val as (typeof PERIODS)[number])
      }}
    >
      <SelectTrigger className="w-[160px]">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {PERIODS.map((p) => (
          <SelectItem key={p} value={p}>
            {t(`period.${p}`)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )

  if (isLoading) {
    return (
      <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
        <div className="flex items-center justify-between">
          <PageHeader title={t('pageTitle')} />
          {periodSelector}
        </div>
        <div className="grid gap-4 sm:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }

  if (!usage || usage.total_tokens === 0) {
    return (
      <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
        <div className="flex items-center justify-between">
          <PageHeader title={t('pageTitle')} />
          {periodSelector}
        </div>
        <EmptyState
          icon={<BarChart3Icon className="size-6" />}
          title={t('empty.title')}
          description={t('empty.description')}
        />
      </div>
    )
  }

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <div className="flex items-center justify-between">
        <PageHeader title={t('pageTitle')} />
        <Select
          value={period}
          onValueChange={(val) => {
            if (val) setPeriod(val)
          }}
        >
          <SelectTrigger className="w-[160px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {PERIODS.map((p) => (
              <SelectItem key={p} value={p}>
                {t(`period.${p}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Summary cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="pb-1">
            <CardTitle className="text-sm font-medium text-foreground/70">
              {t('totalTokens')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <span className="text-2xl font-bold">{usage.total_tokens.toLocaleString()}</span>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-1">
            <CardTitle className="text-sm font-medium text-foreground/70">
              {t('estimatedCost')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <span className="text-2xl font-bold">${usage.estimated_cost_usd.toFixed(2)}</span>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-1">
            <CardTitle className="text-sm font-medium text-foreground/70">
              {t('inputTokens')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <span className="text-2xl font-bold">{usage.prompt_tokens.toLocaleString()}</span>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-1">
            <CardTitle className="text-sm font-medium text-foreground/70">
              {t('outputTokens')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <span className="text-2xl font-bold">{usage.completion_tokens.toLocaleString()}</span>
          </CardContent>
        </Card>
      </div>

      {/* Per-agent breakdown */}
      {usage.by_agent.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>{t('perAgent')}</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('table.agent')}</TableHead>
                  <TableHead className="text-right">{t('table.tokens')}</TableHead>
                  <TableHead className="text-right">{t('table.cost')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {usage.by_agent.map((row) => (
                  <TableRow key={row.agent_id}>
                    <TableCell className="font-medium">
                      <Link
                        href={`/agents/${row.agent_id}`}
                        className="text-primary hover:underline"
                      >
                        {row.agent_name}
                      </Link>
                    </TableCell>
                    <TableCell className="text-right">
                      {row.total_tokens.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right">${row.estimated_cost.toFixed(2)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
