'use client'

import { useState, useMemo } from 'react'
import {
  PlusIcon,
  CpuIcon,
  Trash2Icon,
  PencilIcon,
  Loader2Icon,
  StarIcon,
  ServerIcon,
  InfoIcon,
  EyeIcon,
  WrenchIcon,
  BrainIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useModels, useUpdateModel, useDeleteModel } from '@/lib/hooks/use-models'
import { useProviders, useDeleteProvider } from '@/lib/hooks/use-providers'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Input } from '@/components/ui/input'
import { SearchInput } from '@/components/shared/search-input'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogAction,
  AlertDialogCancel,
} from '@/components/ui/alert-dialog'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Tooltip, TooltipTrigger, TooltipContent } from '@/components/ui/tooltip'
import { EmptyState } from '@/components/shared/empty-state'
import { PageHeader } from '@/components/shared/page-header'
import { ProviderCard } from '@/components/model/provider-card'
import { ProviderForm } from '@/components/model/provider-form'
import { ModelAddDialog } from '@/components/model/model-add-dialog'
import { ModelDetailModal } from '@/components/model/model-detail-modal'
import { getProviderIcon, formatContextWindow } from '@/lib/utils/provider'
import type { Model, Provider } from '@/lib/types'

export default function ModelsPage() {
  const { data: models, isLoading: modelsLoading } = useModels()
  const { data: providers, isLoading: providersLoading } = useProviders()
  const updateModel = useUpdateModel()
  const deleteModel = useDeleteModel()
  const deleteProvider = useDeleteProvider()
  const t = useTranslations('model')
  const tp = useTranslations('provider')
  const tc = useTranslations('common')

  // Provider form state
  const [providerFormOpen, setProviderFormOpen] = useState(false)
  const [editingProvider, setEditingProvider] = useState<Provider | null>(null)

  // Model add dialog state
  const [modelAddOpen, setModelAddOpen] = useState(false)

  // Model edit dialog state
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [editingModel, setEditingModel] = useState<Model | null>(null)
  const [editDisplayName, setEditDisplayName] = useState('')

  // Model search & filter
  const [modelSearch, setModelSearch] = useState('')
  const [providerFilter, setProviderFilter] = useState('all')
  const [capabilityFilter, setCapabilityFilter] = useState('all')

  // Model detail modal state
  const [detailModel, setDetailModel] = useState<Model | null>(null)

  // Model delete confirm dialog state
  const [deletingModelTarget, setDeletingModelTarget] = useState<Model | null>(null)

  const filteredModels = useMemo(() => {
    if (!models) return []
    let result = models
    if (providerFilter !== 'all') {
      result = result.filter((m) => m.provider_id === providerFilter)
    }
    if (capabilityFilter !== 'all') {
      result = result.filter((m) => {
        if (capabilityFilter === 'vision') return m.supports_vision
        if (capabilityFilter === 'function_calling') return m.supports_function_calling
        if (capabilityFilter === 'reasoning') return m.supports_reasoning
        return true
      })
    }
    const q = modelSearch.toLowerCase()
    if (q) {
      result = result.filter(
        (m) =>
          m.display_name.toLowerCase().includes(q) ||
          m.model_name.toLowerCase().includes(q) ||
          m.provider.toLowerCase().includes(q),
      )
    }
    return result
  }, [models, modelSearch, providerFilter, capabilityFilter])

  // Provider delete confirm dialog state
  const [deletingProviderTarget, setDeletingProviderTarget] = useState<Provider | null>(null)

  function openEditProvider(provider: Provider) {
    setEditingProvider(provider)
    setProviderFormOpen(true)
  }

  function openEditModel(model: Model) {
    setEditingModel(model)
    setEditDisplayName(model.display_name)
    setEditDialogOpen(true)
  }

  async function handleEditModelSubmit() {
    if (!editingModel) return
    await updateModel.mutateAsync({
      id: editingModel.id,
      data: { display_name: editDisplayName },
    })
    setEditDialogOpen(false)
    setEditingModel(null)
  }

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader title={t('pageTitle')} />

      <Tabs defaultValue="providers">
        <TabsList variant="line">
          <TabsTrigger value="providers">{t('tab.providers')}</TabsTrigger>
          <TabsTrigger value="models">{t('tab.models')}</TabsTrigger>
        </TabsList>

        {/* Providers Tab */}
        <TabsContent value="providers">
          <div className="space-y-4">
            <div className="flex justify-end">
              <Button
                onClick={() => {
                  setEditingProvider(null)
                  setProviderFormOpen(true)
                }}
              >
                <PlusIcon className="size-4" data-icon="inline-start" />
                {tp('addProvider')}
              </Button>
            </div>

            {providersLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-16 w-full" />
                ))}
              </div>
            ) : providers && providers.length > 0 ? (
              <div className="space-y-2">
                {providers.map((provider) => (
                  <ProviderCard
                    key={provider.id}
                    provider={provider}
                    onEdit={openEditProvider}
                    onDelete={() => setDeletingProviderTarget(provider)}
                    isDeleting={deleteProvider.isPending}
                    deletingId={deleteProvider.variables}
                  />
                ))}
              </div>
            ) : (
              <EmptyState
                icon={<ServerIcon className="size-6" />}
                title={tp('empty.title')}
                description={tp('empty.description')}
              />
            )}
          </div>
        </TabsContent>

        {/* Models Tab */}
        <TabsContent value="models">
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <SearchInput
                containerClassName="flex-1 min-w-[200px]"
                value={modelSearch}
                onChange={(e) => setModelSearch(e.target.value)}
                placeholder={t('searchModels')}
              />
              <Select
                value={providerFilter}
                onValueChange={(val) => {
                  if (val) setProviderFilter(val)
                }}
              >
                <SelectTrigger className="w-[180px]">
                  <SelectValue placeholder={t('allProviders')} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t('allProviders')}</SelectItem>
                  {providers?.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={capabilityFilter}
                onValueChange={(val) => {
                  if (val) setCapabilityFilter(val)
                }}
              >
                <SelectTrigger className="w-[160px]">
                  <SelectValue placeholder={t('allCapabilities')} />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t('allCapabilities')}</SelectItem>
                  <SelectItem value="vision">{t('vision')}</SelectItem>
                  <SelectItem value="function_calling">{t('functionCalling')}</SelectItem>
                  <SelectItem value="reasoning">{t('reasoning')}</SelectItem>
                </SelectContent>
              </Select>
              <Button onClick={() => setModelAddOpen(true)}>
                <PlusIcon className="size-4" data-icon="inline-start" />
                {t('addModel')}
              </Button>
            </div>

            {modelsLoading ? (
              <div className="space-y-2">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-16 w-full" />
                ))}
              </div>
            ) : filteredModels.length > 0 ? (
              <div className="space-y-2">
                {filteredModels.map((model) => (
                  <Card key={model.id}>
                    <CardContent className="flex items-center justify-between py-3">
                      <div className="flex items-center gap-3">
                        <div className="flex size-9 items-center justify-center rounded-lg bg-muted text-xs font-bold text-muted-foreground">
                          {getProviderIcon(model.provider)}
                        </div>
                        <div>
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-sm font-medium">{model.display_name}</span>
                            <Badge variant="outline">{model.provider}</Badge>
                            {model.context_window && (
                              <Badge variant="secondary" className="text-[10px]">
                                {formatContextWindow(model.context_window)}
                              </Badge>
                            )}
                            {model.supports_vision && (
                              <Tooltip>
                                <TooltipTrigger>
                                  <EyeIcon className="size-3.5 text-blue-500" />
                                </TooltipTrigger>
                                <TooltipContent>{t('vision')}</TooltipContent>
                              </Tooltip>
                            )}
                            {model.supports_function_calling && (
                              <Tooltip>
                                <TooltipTrigger>
                                  <WrenchIcon className="size-3.5 text-green-500" />
                                </TooltipTrigger>
                                <TooltipContent>{t('functionCalling')}</TooltipContent>
                              </Tooltip>
                            )}
                            {model.supports_reasoning && (
                              <Tooltip>
                                <TooltipTrigger>
                                  <BrainIcon className="size-3.5 text-purple-500" />
                                </TooltipTrigger>
                                <TooltipContent>{t('reasoning')}</TooltipContent>
                              </Tooltip>
                            )}
                            {model.agent_count > 0 && (
                              <Badge className="border-blue-200 bg-blue-50 text-blue-700 text-[10px] dark:border-blue-800 dark:bg-blue-950 dark:text-blue-300">
                                {t('agentCount', { count: model.agent_count })}
                              </Badge>
                            )}
                            {model.is_default && (
                              <Badge variant="secondary">
                                <StarIcon className="mr-0.5 size-3" />
                                {t('defaultBadge')}
                              </Badge>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground">{model.model_name}</p>
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        <Tooltip>
                          <TooltipTrigger
                            render={
                              <Button
                                variant="ghost"
                                size="icon-sm"
                                aria-label={
                                  model.is_default ? t('alreadyDefault') : t('setDefault')
                                }
                                onClick={() => {
                                  if (!model.is_default) {
                                    updateModel.mutate({
                                      id: model.id,
                                      data: { is_default: true },
                                    })
                                  }
                                }}
                              >
                                <StarIcon
                                  className={`size-4 ${model.is_default ? 'fill-yellow-400 text-yellow-500' : 'text-muted-foreground'}`}
                                />
                              </Button>
                            }
                          />
                          <TooltipContent>
                            {model.is_default ? t('alreadyDefault') : t('setDefault')}
                          </TooltipContent>
                        </Tooltip>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={t('modelDetail')}
                          onClick={() => setDetailModel(model)}
                        >
                          <InfoIcon className="size-4 text-muted-foreground" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={t('editLabel', { name: model.display_name })}
                          onClick={() => openEditModel(model)}
                        >
                          <PencilIcon className="size-4 text-muted-foreground" />
                        </Button>
                        {model.agent_count > 0 ? (
                          <Tooltip>
                            <TooltipTrigger
                              render={
                                <span className="inline-flex size-8 items-center justify-center rounded-md opacity-40">
                                  <Trash2Icon className="size-4 text-muted-foreground" />
                                </span>
                              }
                              aria-label={t('cannotDeleteInUse')}
                            />
                            <TooltipContent>{t('cannotDeleteInUse')}</TooltipContent>
                          </Tooltip>
                        ) : (
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            aria-label={t('deleteLabel', { name: model.display_name })}
                            onClick={() => setDeletingModelTarget(model)}
                            disabled={deleteModel.isPending && deleteModel.variables === model.id}
                          >
                            {deleteModel.isPending && deleteModel.variables === model.id ? (
                              <Loader2Icon className="size-4 animate-spin" />
                            ) : (
                              <Trash2Icon className="size-4 text-muted-foreground" />
                            )}
                          </Button>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : (
              <EmptyState
                icon={<CpuIcon className="size-6" />}
                title={t('empty.title')}
                description={t('empty.description')}
              />
            )}
          </div>
        </TabsContent>
      </Tabs>

      {/* Provider Form Dialog */}
      <ProviderForm
        open={providerFormOpen}
        onOpenChange={(v) => {
          setProviderFormOpen(v)
          if (!v) setEditingProvider(null)
        }}
        editingProvider={editingProvider}
      />

      {/* Model Add Dialog */}
      <ModelAddDialog
        open={modelAddOpen}
        onOpenChange={setModelAddOpen}
        providers={providers ?? []}
      />

      {/* Model Edit Dialog */}
      <Dialog open={editDialogOpen} onOpenChange={setEditDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('dialogTitle.edit')}</DialogTitle>
            <DialogDescription>{t('dialogDescription.edit')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t('displayName')}</label>
              <Input value={editDisplayName} onChange={(e) => setEditDisplayName(e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button
              onClick={handleEditModelSubmit}
              disabled={!editDisplayName.trim() || updateModel.isPending}
            >
              {updateModel.isPending && <Loader2Icon className="mr-1 size-4 animate-spin" />}
              {tc('save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Model Detail Modal */}
      {detailModel && (
        <ModelDetailModal
          model={detailModel}
          open={!!detailModel}
          onClose={() => setDetailModel(null)}
        />
      )}

      {/* Model Delete Confirm */}
      <AlertDialog
        open={!!deletingModelTarget}
        onOpenChange={(v) => !v && setDeletingModelTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('deleteConfirm')}</AlertDialogTitle>
            <AlertDialogDescription>{deletingModelTarget?.display_name}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc('cancel')}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (deletingModelTarget) {
                  deleteModel.mutate(deletingModelTarget.id)
                  setDeletingModelTarget(null)
                }
              }}
            >
              {tc('delete')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Provider Delete Confirm */}
      <AlertDialog
        open={!!deletingProviderTarget}
        onOpenChange={(v) => !v && setDeletingProviderTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{tp('deleteConfirm')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('deleteProviderWarning', { count: deletingProviderTarget?.model_count ?? 0 })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc('cancel')}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                if (deletingProviderTarget) {
                  deleteProvider.mutate(deletingProviderTarget.id)
                  setDeletingProviderTarget(null)
                }
              }}
            >
              {tc('delete')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
