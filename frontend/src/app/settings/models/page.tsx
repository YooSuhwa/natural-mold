'use client'

import { useMemo, useState, useSyncExternalStore } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Plus } from 'lucide-react'

import { announceHealthResult } from '@/lib/health-check-toast'
import { useCredentials } from '@/lib/hooks/use-credentials'
import { filterLlmCredentials, resolveCredentialForModel } from '@/lib/utils/credential-resolution'

import { Button } from '@/components/ui/button'
import { ResourcePage, ResourcePanel } from '@/components/shared/resource-layout'
import { DataTable, type FilterDef } from '@/components/ui/data-table'
import { EmptyState } from '@/components/shared/empty-state'
import { ModelAddDialog } from '@/components/model/model-add-dialog'
import { ModelEditDialog } from '@/components/model/model-edit-dialog'
import { ModelTestDialog } from '@/components/model/model-test-dialog'
import { ModelTestBulkDialog } from '@/components/model/model-test-bulk-dialog'
import { useModels } from '@/lib/hooks/use-models'
import { useSession } from '@/lib/auth/session'
import { useModelHealth, useRunHealthCheck } from '@/lib/hooks/use-health'
import type { Model } from '@/lib/types/model'
import type { HealthCheckEntry } from '@/lib/types/health'
import { SettingsShell } from '../_components/settings-shell'
import { modelHasAnyRanking } from './_components/model-catalog-cells'
import { useModelCatalogColumns } from './_components/model-catalog-columns'
import { ModelCatalogToolbar } from './_components/model-catalog-toolbar'

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

  const columns = useModelCatalogColumns({
    healthByModel,
    checkPending: runHealthCheck.isPending,
    onCheckNow: handleCheckNow,
    onTest: setTesting,
  })

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
    <ModelCatalogToolbar
      onlyWithRanking={onlyWithRanking}
      onOnlyWithRankingChange={setOnlyWithRanking}
      showOperatorControls={showOperatorControls}
      showHidden={showHidden}
      onShowHiddenChange={setShowHidden}
      selectedCount={selected.length}
      onTestSelected={() => setBulkTestOpen(true)}
    />
  )

  return (
    <SettingsShell wide className="max-w-7xl">
      <ResourcePage
        title={t('catalog.title')}
        description={t('catalog.description')}
        variant="embedded"
        contentClassName="pb-20"
        action={
          // Add/edit/delete are operator actions (plan 13.9 models row; backend SUPER).
          showOperatorControls ? (
            <Button onClick={() => setAddOpen(true)}>
              <Plus className="size-4" />
              {t('catalog.new')}
            </Button>
          ) : null
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
                  showOperatorControls ? (
                    <Button onClick={() => setAddOpen(true)}>
                      <Plus className="size-4" />
                      {t('addModel')}
                    </Button>
                  ) : undefined
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
                onRowClick={showOperatorControls ? (row) => setEditing(row) : undefined}
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
