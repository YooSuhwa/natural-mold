'use client'

import { useMemo, useState, useSyncExternalStore } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Activity, Plus, Eye, EyeOff, Wrench, Lightbulb, Zap } from 'lucide-react'
import type { ColumnDef } from '@tanstack/react-table'

import { announceHealthResult } from '@/lib/health-check-toast'
import { useCredentials } from '@/lib/hooks/use-credentials'
import { filterLlmCredentials, resolveCredentialForModel } from '@/lib/utils/credential-resolution'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ResourcePage, ResourcePanel } from '@/components/shared/resource-layout'
import { DataTable, type FilterDef } from '@/components/ui/data-table'
import { DomainIcon } from '@/components/shared/icon'
import { EmptyState } from '@/components/shared/empty-state'
import { StatusChip } from '@/components/shared/status-chip'
import { ModelSourceBadge } from '@/components/model/model-source-badge'
import { ModelAddDialog } from '@/components/model/model-add-dialog'
import { ModelEditDialog } from '@/components/model/model-edit-dialog'
import { ModelTestDialog } from '@/components/model/model-test-dialog'
import { ModelTestBulkDialog } from '@/components/model/model-test-bulk-dialog'
import { formatTokenPrice } from '@/components/model/model-format'
import { RankingBadge } from '@/components/model/model-rankings'
import { Checkbox } from '@/components/ui/checkbox'
import { useModels } from '@/lib/hooks/use-models'
import { useSession } from '@/lib/auth/session'
import { useModelHealth, useRunHealthCheck } from '@/lib/hooks/use-health'
import { formatDisplayNumber } from '@/lib/utils/display-format'
import type { Model } from '@/lib/types/model'
import type { HealthCheckEntry } from '@/lib/types/health'
import { SettingsShell } from '../_components/settings-shell'

const subscribeHydration = () => () => undefined
const getHydratedSnapshot = () => true
const getServerHydratedSnapshot = () => false

export default function ModelsPage() {
  const t = useTranslations('model')
  const { data: user } = useSession()
  const isSuper = Boolean(user?.is_super_user)
  const sessionHydrated = useSyncExternalStore(
    subscribeHydration,
    getHydratedSnapshot,
    getServerHydratedSnapshot,
  )
  // Default ON for operators so the new visibility column is discoverable;
  // regular users can't pass include_hidden (backend would 403) so we pin
  // the toggle off + disabled for them.
  const [showHidden, setShowHidden] = useState(true)
  const showOperatorControls = sessionHydrated && isSuper
  const effectiveShowHidden = showOperatorControls && showHidden
  const { data: models, isLoading } = useModels({
    includeHidden: effectiveShowHidden,
  })
  const { data: healthEntries } = useModelHealth()
  const { data: credentials } = useCredentials()
  const runHealthCheck = useRunHealthCheck()
  const [addOpen, setAddOpen] = useState(false)
  const [editing, setEditing] = useState<Model | null>(null)
  const [testing, setTesting] = useState<Model | null>(null)
  const [bulkTestOpen, setBulkTestOpen] = useState(false)
  const [selected, setSelected] = useState<Model[]>([])
  const [onlyWithRanking, setOnlyWithRanking] = useState(false)

  // Stable reference for downstream memos. `models ?? []` would create a fresh
  // array on every render and bust the providerOptions / sourceOptions cache.
  const allModels = useMemo<Model[]>(() => models ?? [], [models])

  // Optional "Has ranking" filter — narrows the catalog to models with at
  // least one populated benchmark score. Applied before the DataTable so the
  // pagination/empty-state reflect the filtered set.
  const data = useMemo<Model[]>(() => {
    if (!onlyWithRanking) return allModels
    return allModels.filter((m) => modelHasAnyRanking(m))
  }, [allModels, onlyWithRanking])

  // O(1) lookup of latest health entry per model_id. Falls back to "unknown"
  // when no probe has been recorded yet (e.g. freshly added model).
  const healthByModel = useMemo(() => {
    const map = new Map<string, HealthCheckEntry>()
    ;(healthEntries ?? []).forEach((h) => map.set(h.target_id, h))
    return map
  }, [healthEntries])

  const llmCredentials = useMemo(() => filterLlmCredentials(credentials), [credentials])

  async function handleCheckNow(model: Model) {
    // Tiered fallback (default_credential_id → provider match → first LLM)
    // shared with ModelHealthPanel and ModelTestDialog so all three surfaces
    // pick the same credential for the same model.
    const credentialId = resolveCredentialForModel(model, llmCredentials)
    if (!credentialId) {
      toast.error(t('catalog.toast.noCredential'))
      return
    }
    try {
      const result = await runHealthCheck.mutateAsync({
        targetKind: 'model',
        targetId: model.id,
        credentialId,
      })
      announceHealthResult(result)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('catalog.toast.healthCheckFailed'))
    }
  }

  const providerOptions = useMemo(() => {
    const set = new Set<string>()
    data.forEach((m) => set.add(m.provider))
    return Array.from(set).sort()
  }, [data])

  const sourceOptions = useMemo(() => {
    const set = new Set<string>()
    data.forEach((m) => {
      if (m.source) set.add(m.source)
    })
    return Array.from(set).sort()
  }, [data])

  const columns = useMemo<ColumnDef<Model>[]>(
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
                handleCheckNow(row.original)
              }}
              disabled={runHealthCheck.isPending}
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
                setTesting(row.original)
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
    [healthByModel, runHealthCheck.isPending, t],
  )

  const filters = useMemo<FilterDef[]>(
    () => [
      {
        columnId: 'provider',
        label: t('provider'),
        options: providerOptions.map((p) => ({ value: p, label: p })),
      },
      {
        columnId: 'source',
        label: t('catalog.columns.source'),
        options: sourceOptions.map((s) => ({
          value: s,
          label: s.charAt(0).toUpperCase() + s.slice(1),
        })),
      },
      {
        columnId: 'health',
        label: t('catalog.columns.status'),
        options: [
          { value: 'healthy', label: t('catalog.health.healthy') },
          { value: 'degraded', label: t('catalog.health.degraded') },
          { value: 'unhealthy', label: t('catalog.health.unhealthy') },
          { value: 'unknown', label: t('catalog.health.unknown') },
        ],
      },
    ],
    [providerOptions, sourceOptions, t],
  )

  const toolbar = (
    <div className="flex items-center gap-3">
      <label
        htmlFor="only-with-ranking"
        className="inline-flex cursor-pointer items-center gap-2 text-xs text-muted-foreground"
      >
        <Checkbox
          id="only-with-ranking"
          data-testid="only-with-ranking"
          checked={onlyWithRanking}
          onCheckedChange={(v) => setOnlyWithRanking(Boolean(v))}
        />
        {t('catalog.filters.hasBenchmark')}
      </label>
      {showOperatorControls && (
        <label
          htmlFor="show-hidden"
          className="inline-flex cursor-pointer items-center gap-2 text-xs text-muted-foreground"
        >
          <Checkbox
            id="show-hidden"
            data-testid="show-hidden"
            checked={showHidden}
            onCheckedChange={(v) => setShowHidden(Boolean(v))}
          />
          {t('catalog.filters.showHidden')}
        </label>
      )}
      {selected.length > 0 && (
        <Button size="sm" onClick={() => setBulkTestOpen(true)} data-testid="test-selected">
          <Zap className="size-3.5" />
          {t('catalog.actions.testSelected')}
          <Badge variant="secondary" className="ml-1">
            {selected.length}
          </Badge>
        </Button>
      )}
    </div>
  )

  return (
    <SettingsShell wide className="max-w-7xl">
      <ResourcePage
        title={t('catalog.title')}
        description={t('catalog.description')}
        variant="embedded"
        contentClassName="pb-20"
        action={
          <Button onClick={() => setAddOpen(true)}>
            <Plus className="size-4" />
            {t('catalog.new')}
          </Button>
        }
      >
        <ResourcePanel>
          <ResourcePanel.Body className="bg-background/25">
            {!isLoading && allModels.length === 0 ? (
              <EmptyState
                iconId="model"
                title={t('catalog.empty.title')}
                description={t('catalog.empty.description')}
                action={
                  <Button onClick={() => setAddOpen(true)}>
                    <Plus className="size-4" />
                    {t('addModel')}
                  </Button>
                }
              />
            ) : (
              <DataTable
                columns={columns}
                data={data}
                loading={isLoading}
                searchable
                searchPlaceholder={t('catalog.searchPlaceholder')}
                globalFilterFn={(row, query) => {
                  const m = row as Model
                  return (
                    m.display_name.toLowerCase().includes(query) ||
                    m.model_name.toLowerCase().includes(query)
                  )
                }}
                filters={filters}
                enableRowSelection
                onRowSelectionChange={setSelected}
                toolbar={toolbar}
                onRowClick={(row) => setEditing(row)}
                emptyTitle={t('catalog.empty.filtered')}
              />
            )}
          </ResourcePanel.Body>
        </ResourcePanel>

        <ModelAddDialog open={addOpen} onOpenChange={setAddOpen} />
        <ModelEditDialog
          model={editing}
          open={!!editing}
          onOpenChange={(open) => !open && setEditing(null)}
        />
        <ModelTestDialog
          model={testing}
          open={!!testing}
          onOpenChange={(open) => !open && setTesting(null)}
        />
        <ModelTestBulkDialog models={selected} open={bulkTestOpen} onOpenChange={setBulkTestOpen} />
      </ResourcePage>
    </SettingsShell>
  )
}

function HealthCell({
  entry,
  format,
}: {
  entry: HealthCheckEntry | undefined
  format: (iso: string) => string
}) {
  if (!entry) {
    return <StatusChip variant="unknown" />
  }
  return (
    <div className="flex flex-col items-start gap-0.5">
      <StatusChip variant={entry.status} />
      <span className="moldy-ui-micro text-muted-foreground">{format(entry.checked_at)}</span>
    </div>
  )
}

function formatRelativeTime(
  iso: string,
  t: (
    key:
      | 'relativeTime.secondsAgo'
      | 'relativeTime.minutesAgo'
      | 'relativeTime.hoursAgo'
      | 'relativeTime.daysAgo',
    values: { count: number },
  ) => string,
): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const deltaSec = Math.floor((Date.now() - then) / 1000)
  if (deltaSec < 60) return t('relativeTime.secondsAgo', { count: deltaSec })
  if (deltaSec < 3600) return t('relativeTime.minutesAgo', { count: Math.floor(deltaSec / 60) })
  if (deltaSec < 86400) return t('relativeTime.hoursAgo', { count: Math.floor(deltaSec / 3600) })
  return t('relativeTime.daysAgo', { count: Math.floor(deltaSec / 86400) })
}

/**
 * True when at least one benchmark score is present. Used by the "Has
 * ranking" toggle so we can hide unmatched/Custom-ID models on demand.
 */
function modelHasAnyRanking(model: Model): boolean {
  const r = model.rankings
  if (!r) return false
  return (
    typeof r.lmarena === 'number' ||
    typeof r.livebench === 'number' ||
    typeof r.aa_index === 'number'
  )
}

function CapabilityIcons({ model }: { model: Model }) {
  const t = useTranslations('model')
  const items = [
    {
      key: 'vision',
      icon: Eye,
      enabled: Boolean(model.supports_vision),
      title: t('vision'),
    },
    {
      key: 'tools',
      icon: Wrench,
      enabled: Boolean(model.supports_function_calling),
      title: t('functionCalling'),
    },
    {
      key: 'reasoning',
      icon: Lightbulb,
      enabled: Boolean(model.supports_reasoning),
      title: t('reasoning'),
    },
  ].filter((i) => i.enabled)

  if (items.length === 0) return null

  return (
    <div className="mt-0.5 flex items-center gap-1 text-muted-foreground">
      {items.map(({ key, icon: Icon, title }) => (
        <Icon key={key} className="size-3" aria-label={title} />
      ))}
    </div>
  )
}
