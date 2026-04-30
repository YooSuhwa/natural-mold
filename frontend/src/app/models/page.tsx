'use client'

import { useMemo, useState } from 'react'
import { toast } from 'sonner'
import { Activity, Plus, Brain, Eye, Wrench, Lightbulb, Zap } from 'lucide-react'
import type { ColumnDef } from '@tanstack/react-table'

import { announceHealthResult } from '@/lib/health-check-toast'
import { useCredentials } from '@/lib/hooks/use-credentials'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { PageHeader } from '@/components/shared/page-header'
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
import {
  RANKING_META,
  RankingCell,
  RankingHeader,
} from '@/components/model/model-rankings'
import { Checkbox } from '@/components/ui/checkbox'
import { useModels } from '@/lib/hooks/use-models'
import { useModelHealth, useRunHealthCheck } from '@/lib/hooks/use-health'
import type { Model } from '@/lib/types/model'
import type { HealthCheckEntry } from '@/lib/types/health'

export default function ModelsPage() {
  const { data: models, isLoading } = useModels()
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

  // Pick the LLM credential whose definition matches the model's provider.
  // Without this the backend falls back to the env ``OPENAI_API_KEY``, which
  // is usually a different (wrong) key.
  const credentialForProvider = useMemo(() => {
    const llmKeys = new Set([
      'openai',
      'anthropic',
      'google_genai',
      'azure_openai',
      'openrouter',
      'openai_compatible',
    ])
    return (provider: string): string | undefined => {
      const llmCreds = (credentials ?? []).filter((c) => llmKeys.has(c.definition_key))
      const exact = llmCreds.find((c) => c.definition_key === provider)
      return (exact ?? llmCreds[0])?.id
    }
  }, [credentials])

  async function handleCheckNow(model: Model) {
    const credentialId = credentialForProvider(model.provider)
    if (!credentialId) {
      toast.error('No LLM credential available — register one first.')
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
      toast.error(e instanceof Error ? e.message : 'Health check failed')
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
        id: 'display_name',
        accessorKey: 'display_name',
        header: 'Model',
        cell: ({ row }) => (
          <div className="flex items-center gap-2">
            <DomainIcon iconId={row.original.provider} className="size-4" />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">
                {row.original.display_name}
                {row.original.is_default && (
                  <span className="ml-2 inline-flex items-center rounded-full bg-emerald-100 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 ring-1 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-300 dark:ring-emerald-500/30">
                    default
                  </span>
                )}
              </p>
              <CapabilityIcons model={row.original} />
            </div>
          </div>
        ),
      },
      {
        id: 'model_name',
        accessorKey: 'model_name',
        header: 'ID',
        cell: ({ row }) => (
          <span className="font-mono text-xs text-muted-foreground">{row.original.model_name}</span>
        ),
      },
      {
        id: 'provider',
        accessorKey: 'provider',
        header: 'Provider',
        cell: ({ row }) => <span className="text-xs">{row.original.provider}</span>,
        filterFn: 'equals',
      },
      {
        id: 'cost_in',
        accessorFn: (row) => row.cost_per_input_token ?? 0,
        header: 'Input',
        cell: ({ row }) => (
          <span className="font-mono text-xs tabular-nums">
            {formatTokenPrice(row.original.cost_per_input_token)}
          </span>
        ),
      },
      {
        id: 'cost_out',
        accessorFn: (row) => row.cost_per_output_token ?? 0,
        header: 'Output',
        cell: ({ row }) => (
          <span className="font-mono text-xs tabular-nums">
            {formatTokenPrice(row.original.cost_per_output_token)}
          </span>
        ),
      },
      {
        id: 'context_window',
        accessorFn: (row) => row.context_window ?? 0,
        header: 'Context',
        cell: ({ row }) =>
          row.original.context_window ? (
            <span className="font-mono text-xs tabular-nums">
              {row.original.context_window.toLocaleString()}
            </span>
          ) : (
            <span className="text-xs text-muted-foreground">—</span>
          ),
      },
      // M11 — Benchmark rankings. Missing scores are normalised to
      // `undefined` so TanStack's `sortUndefined: 'last'` keeps them pinned
      // to the bottom regardless of sort direction. Headers carry an ⓘ
      // tooltip explaining what each score represents.
      {
        id: 'lmarena',
        accessorFn: (row) => row.rankings?.lmarena ?? undefined,
        header: () => <RankingHeader rankingKey="lmarena" />,
        cell: ({ row }) => (
          <RankingCell
            value={row.original.rankings?.lmarena}
            format={RANKING_META.lmarena.format}
          />
        ),
        sortingFn: 'basic',
        sortUndefined: 'last',
      },
      {
        id: 'livebench',
        accessorFn: (row) => row.rankings?.livebench ?? undefined,
        header: () => <RankingHeader rankingKey="livebench" />,
        cell: ({ row }) => (
          <RankingCell
            value={row.original.rankings?.livebench}
            format={RANKING_META.livebench.format}
          />
        ),
        sortingFn: 'basic',
        sortUndefined: 'last',
      },
      {
        id: 'aa_index',
        accessorFn: (row) => row.rankings?.aa_index ?? undefined,
        header: () => <RankingHeader rankingKey="aa_index" />,
        cell: ({ row }) => (
          <RankingCell
            value={row.original.rankings?.aa_index}
            format={RANKING_META.aa_index.format}
          />
        ),
        sortingFn: 'basic',
        sortUndefined: 'last',
      },
      {
        id: 'source',
        accessorKey: 'source',
        header: 'Source',
        cell: ({ row }) => <ModelSourceBadge source={row.original.source} />,
        filterFn: (row, _columnId, filterValue) => {
          if (filterValue === undefined || filterValue === null) return true
          return row.original.source === filterValue
        },
      },
      {
        id: 'health',
        accessorFn: (row) => healthByModel.get(row.id)?.status ?? 'unknown',
        header: 'Status',
        cell: ({ row }) => {
          const entry = healthByModel.get(row.original.id)
          return <HealthCell entry={entry} />
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
        header: 'Agents',
        cell: ({ row }) => <span className="text-xs tabular-nums">{row.original.agent_count}</span>,
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              aria-label={`Check ${row.original.display_name}`}
              data-testid={`check-now-${row.original.id}`}
              onClick={(e) => {
                e.stopPropagation()
                handleCheckNow(row.original)
              }}
              disabled={runHealthCheck.isPending}
            >
              <Activity className="size-3.5" /> Check
            </Button>
            <Button
              variant="ghost"
              size="sm"
              aria-label={`Test ${row.original.display_name}`}
              onClick={(e) => {
                e.stopPropagation()
                setTesting(row.original)
              }}
            >
              <Zap className="size-3.5" /> Test
            </Button>
          </div>
        ),
        enableSorting: false,
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [healthByModel, runHealthCheck.isPending],
  )

  const filters = useMemo<FilterDef[]>(
    () => [
      {
        columnId: 'provider',
        label: 'Provider',
        options: providerOptions.map((p) => ({ value: p, label: p })),
      },
      {
        columnId: 'source',
        label: 'Source',
        options: sourceOptions.map((s) => ({
          value: s,
          label: s.charAt(0).toUpperCase() + s.slice(1),
        })),
      },
      {
        columnId: 'health',
        label: 'Status',
        options: [
          { value: 'healthy', label: 'Healthy' },
          { value: 'degraded', label: 'Degraded' },
          { value: 'unhealthy', label: 'Unhealthy' },
          { value: 'unknown', label: 'Unknown' },
        ],
      },
    ],
    [providerOptions, sourceOptions],
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
        Has ranking
      </label>
      {selected.length > 0 && (
        <Button size="sm" onClick={() => setBulkTestOpen(true)} data-testid="test-selected">
          <Zap className="size-3.5" /> Test Selected
          <Badge variant="secondary" className="ml-1">
            {selected.length}
          </Badge>
        </Button>
      )}
    </div>
  )

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader
        title="Models"
        description="LLM catalog with pricing, capability flags, and per-credential discovery."
        action={
          <Button onClick={() => setAddOpen(true)}>
            <Plus className="size-4" />
            New model
          </Button>
        }
      />

      {!isLoading && allModels.length === 0 ? (
        <EmptyState
          icon={<Brain className="size-6" />}
          title="No models yet"
          description="Discover models from a saved LLM credential, or enter a custom ID."
          action={
            <Button onClick={() => setAddOpen(true)}>
              <Plus className="size-4" />
              Add model
            </Button>
          }
        />
      ) : (
        <DataTable
          columns={columns}
          data={data}
          loading={isLoading}
          searchable
          searchPlaceholder="Search by name or model ID"
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
          emptyTitle="No models match your filters"
        />
      )}

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
    </div>
  )
}

function HealthCell({ entry }: { entry: HealthCheckEntry | undefined }) {
  if (!entry) {
    return <StatusChip variant="unknown" />
  }
  return (
    <div className="flex flex-col items-start gap-0.5">
      <StatusChip variant={entry.status} />
      <span className="text-[10px] text-muted-foreground">
        {formatRelativeTime(entry.checked_at)}
      </span>
    </div>
  )
}

function formatRelativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return iso
  const deltaSec = Math.floor((Date.now() - then) / 1000)
  if (deltaSec < 60) return `${deltaSec}s ago`
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`
  return `${Math.floor(deltaSec / 86400)}d ago`
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
  const items = [
    {
      key: 'vision',
      icon: Eye,
      enabled: Boolean(model.supports_vision),
      title: 'Vision',
    },
    {
      key: 'tools',
      icon: Wrench,
      enabled: Boolean(model.supports_function_calling),
      title: 'Function calling',
    },
    {
      key: 'reasoning',
      icon: Lightbulb,
      enabled: Boolean(model.supports_reasoning),
      title: 'Reasoning',
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
