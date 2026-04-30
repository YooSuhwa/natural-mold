'use client'

import { useMemo, useState } from 'react'
import { Plus, Brain, Eye, Wrench, Lightbulb, Zap } from 'lucide-react'
import type { ColumnDef } from '@tanstack/react-table'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { PageHeader } from '@/components/shared/page-header'
import { DataTable, type FilterDef } from '@/components/ui/data-table'
import { DomainIcon } from '@/components/shared/icon'
import { EmptyState } from '@/components/shared/empty-state'
import { ModelSourceBadge } from '@/components/model/model-source-badge'
import { ModelAddDialog } from '@/components/model/model-add-dialog'
import { ModelEditDialog } from '@/components/model/model-edit-dialog'
import { ModelTestDialog } from '@/components/model/model-test-dialog'
import { ModelTestBulkDialog } from '@/components/model/model-test-bulk-dialog'
import { formatTokenPrice } from '@/components/model/model-format'
import { useModels } from '@/lib/hooks/use-models'
import type { Model } from '@/lib/types/model'

export default function ModelsPage() {
  const { data: models, isLoading } = useModels()
  const [addOpen, setAddOpen] = useState(false)
  const [editing, setEditing] = useState<Model | null>(null)
  const [testing, setTesting] = useState<Model | null>(null)
  const [bulkTestOpen, setBulkTestOpen] = useState(false)
  const [selected, setSelected] = useState<Model[]>([])

  // Stable reference for downstream memos. `models ?? []` would create a fresh
  // array on every render and bust the providerOptions / sourceOptions cache.
  const data = useMemo<Model[]>(() => models ?? [], [models])

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
          <span className="font-mono text-xs text-muted-foreground">
            {row.original.model_name}
          </span>
        ),
      },
      {
        id: 'provider',
        accessorKey: 'provider',
        header: 'Provider',
        cell: ({ row }) => (
          <span className="text-xs">{row.original.provider}</span>
        ),
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
        id: 'agent_count',
        accessorKey: 'agent_count',
        header: 'Agents',
        cell: ({ row }) => (
          <span className="text-xs tabular-nums">
            {row.original.agent_count}
          </span>
        ),
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => (
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
        ),
        enableSorting: false,
      },
    ],
    [],
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
    ],
    [providerOptions, sourceOptions],
  )

  const toolbar =
    selected.length > 0 ? (
      <Button
        size="sm"
        onClick={() => setBulkTestOpen(true)}
        data-testid="test-selected"
      >
        <Zap className="size-3.5" /> Test Selected
        <Badge variant="secondary" className="ml-1">
          {selected.length}
        </Badge>
      </Button>
    ) : null

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

      {!isLoading && data.length === 0 ? (
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
      <ModelTestBulkDialog
        models={selected}
        open={bulkTestOpen}
        onOpenChange={setBulkTestOpen}
      />
    </div>
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
        <Icon
          key={key}
          className="size-3"
          aria-label={title}
        />
      ))}
    </div>
  )
}
