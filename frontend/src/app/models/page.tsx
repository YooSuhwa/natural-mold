'use client'

import { useState, useMemo } from 'react'
import {
  PlusIcon,
  CpuIcon,
  Trash2Icon,
  PencilIcon,
  Loader2Icon,
  StarIcon,
  SearchIcon,
  ServerIcon,
} from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useModels, useUpdateModel, useDeleteModel } from '@/lib/hooks/use-models'
import { useProviders, useDeleteProvider } from '@/lib/hooks/use-providers'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { EmptyState } from '@/components/shared/empty-state'
import { PageHeader } from '@/components/shared/page-header'
import { ProviderCard } from '@/components/model/provider-card'
import { ProviderForm } from '@/components/model/provider-form'
import { ModelAddDialog } from '@/components/model/model-add-dialog'
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

  const filteredModels = useMemo(() => {
    if (!models) return []
    let result = models
    if (providerFilter !== 'all') {
      result = result.filter((m) => m.provider_id === providerFilter)
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
  }, [models, modelSearch, providerFilter])

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
                    onDelete={(id) => deleteProvider.mutate(id)}
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
            <div className="flex items-center gap-3">
              <div className="relative flex-1">
                <SearchIcon className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={modelSearch}
                  onChange={(e) => setModelSearch(e.target.value)}
                  placeholder={t('searchModels')}
                  className="pl-9"
                />
              </div>
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
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium">{model.display_name}</span>
                            <Badge variant="outline">{model.provider}</Badge>
                            {model.context_window && (
                              <Badge variant="secondary" className="text-[10px]">
                                {formatContextWindow(model.context_window)}
                              </Badge>
                            )}
                            {model.input_modalities?.map((m) => (
                              <Badge key={m} variant="ghost" className="text-[10px]">
                                {m}
                              </Badge>
                            ))}
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
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={t('editLabel', { name: model.display_name })}
                          onClick={() => openEditModel(model)}
                        >
                          <PencilIcon className="size-4 text-muted-foreground" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={t('deleteLabel', { name: model.display_name })}
                          onClick={() => {
                            if (window.confirm(t('deleteConfirm'))) {
                              deleteModel.mutate(model.id)
                            }
                          }}
                          disabled={deleteModel.isPending && deleteModel.variables === model.id}
                        >
                          {deleteModel.isPending && deleteModel.variables === model.id ? (
                            <Loader2Icon className="size-4 animate-spin" />
                          ) : (
                            <Trash2Icon className="size-4 text-muted-foreground" />
                          )}
                        </Button>
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
    </div>
  )
}
