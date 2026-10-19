'use client'

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'
import { Activity, EyeOff, Zap } from 'lucide-react'
import type { ColumnDef } from '@tanstack/react-table'

import { Button } from '@/components/ui/button'
import { DomainIcon } from '@/components/shared/icon'
import { ModelSourceBadge } from '@/components/model/model-source-badge'
import { formatTokenPrice } from '@/components/model/model-format'
import { RankingBadge } from '@/components/model/model-rankings'
import { formatDisplayNumber } from '@/lib/utils/display-format'
import type { Model } from '@/lib/types/model'
import type { HealthCheckEntry } from '@/lib/types/health'

import {
  CapabilityIcons,
  HealthCell,
  formatRelativeTime,
  modelHasAnyRanking,
} from './model-catalog-cells'

interface ColumnOptions {
  healthByModel: Map<string, HealthCheckEntry>
  checkPending: boolean
  onCheckNow: (model: Model) => void
  onTest: (model: Model) => void
}

/** Model catalog table columns (moved out of the models page, H-05 size cap). */
export function useModelCatalogColumns({
  healthByModel,
  checkPending,
  onCheckNow,
  onTest,
}: ColumnOptions): ColumnDef<Model>[] {
  const t = useTranslations('model')
  return useMemo<ColumnDef<Model>[]>(
    () => [
      {
        id: 'provider',
        accessorKey: 'provider',
        header: t('catalog.columns.model'),
        cell: ({ row }) => (
          <div
            className={`flex min-w-56 items-center gap-2 ${
              row.original.is_visible ? '' : 'opacity-60'
            }`}
          >
            <DomainIcon iconId={row.original.provider} className="size-4" />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">
                {row.original.display_name}
                {row.original.is_default && (
                  <span className="moldy-status-surface moldy-status-success ml-2 inline-flex items-center rounded-full px-1.5 py-0.5 moldy-ui-micro font-medium">
                    {t('defaultBadge')}
                  </span>
                )}
                {!row.original.is_visible && (
                  <span className="ml-2 inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 moldy-ui-micro font-medium text-muted-foreground ring-1 ring-border">
                    <EyeOff className="size-3" />
                    {t('catalog.hiddenBadge')}
                  </span>
                )}
              </p>
              <p className="truncate font-mono moldy-ui-caption text-muted-foreground">
                {row.original.provider} · {row.original.model_name}
              </p>
              <CapabilityIcons model={row.original} />
            </div>
          </div>
        ),
        filterFn: 'equals',
      },
      {
        id: 'cost',
        accessorFn: (row) => row.cost_per_input_token ?? 0,
        header: t('catalog.columns.price'),
        cell: ({ row }) => (
          <div className="flex min-w-32 flex-col gap-0.5 font-mono moldy-ui-caption tabular-nums">
            <span>
              <span className="mr-1 font-sans text-muted-foreground">{t('inputModalities')}</span>
              {formatTokenPrice(row.original.cost_per_input_token)}
            </span>
            <span>
              <span className="mr-1 font-sans text-muted-foreground">{t('outputModalities')}</span>
              {formatTokenPrice(row.original.cost_per_output_token)}
            </span>
          </div>
        ),
      },
      {
        id: 'context_window',
        accessorFn: (row) => row.context_window ?? 0,
        header: t('catalog.columns.context'),
        cell: ({ row }) =>
          row.original.context_window ? (
            <span className="font-mono text-xs tabular-nums">
              {formatDisplayNumber(row.original.context_window)}
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">—</span>
          ),
      },
      {
        id: 'benchmarks',
        accessorFn: (row) => (modelHasAnyRanking(row) ? 1 : 0),
        header: t('catalog.columns.benchmarks'),
        cell: ({ row }) => {
          const rankings = row.original.rankings
          if (!modelHasAnyRanking(row.original)) {
            return <span className="text-xs text-muted-foreground">—</span>
          }
          return (
            <div className="flex min-w-32 flex-wrap gap-1">
              <RankingBadge rankingKey="lmarena" value={rankings?.lmarena} />
              <RankingBadge rankingKey="livebench" value={rankings?.livebench} />
              <RankingBadge rankingKey="aa_index" value={rankings?.aa_index} />
            </div>
          )
        },
      },
      {
        id: 'source',
        accessorKey: 'source',
        header: t('catalog.columns.source'),
        cell: ({ row }) => <ModelSourceBadge source={row.original.source} />,
        filterFn: (row, _columnId, filterValue) => {
          if (filterValue === undefined || filterValue === null) return true
          return row.original.source === filterValue
        },
      },
      {
        id: 'health',
        accessorFn: (row) => healthByModel.get(row.id)?.status ?? 'unknown',
        header: t('catalog.columns.status'),
        cell: ({ row }) => {
          const entry = healthByModel.get(row.original.id)
          return <HealthCell entry={entry} format={(iso) => formatRelativeTime(iso, t)} />
        },
        filterFn: (row, _columnId, filterValue) => {
          if (filterValue === undefined || filterValue === null) return true
          const status = healthByModel.get(row.original.id)?.status ?? 'unknown'
          return status === filterValue
        },
      },
      {
        id: 'agent_count',
        accessorKey: 'agent_count',
        header: t('catalog.columns.agents'),
        cell: ({ row }) => <span className="text-xs tabular-nums">{row.original.agent_count}</span>,
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon-sm"
              className="px-2"
              aria-label={t('catalog.actions.checkNowFor', { name: row.original.display_name })}
              title={t('catalog.actions.checkNow')}
              data-testid={`check-now-${row.original.id}`}
              onClick={(e) => {
                e.stopPropagation()
                onCheckNow(row.original)
              }}
              disabled={checkPending}
            >
              <Activity className="size-3.5" />
              <span className="sr-only">{t('catalog.actions.checkNow')}</span>
            </Button>
            <Button
              variant="ghost"
              size="icon-sm"
              className="px-2"
              aria-label={t('catalog.actions.testFor', { name: row.original.display_name })}
              title={t('catalog.actions.test')}
              onClick={(e) => {
                e.stopPropagation()
                onTest(row.original)
              }}
            >
              <Zap className="size-3.5" />
              <span className="sr-only">{t('catalog.actions.test')}</span>
            </Button>
          </div>
        ),
        enableSorting: false,
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [healthByModel, checkPending, t],
  )
}
