'use client'

import { useState } from 'react'
import { PlusIcon, CpuIcon, Trash2Icon, PencilIcon, Loader2Icon, StarIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { useModels, useCreateModel, useUpdateModel, useDeleteModel } from '@/lib/hooks/use-models'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogTrigger,
} from '@/components/ui/dialog'
import { EmptyState } from '@/components/shared/empty-state'
import { PageHeader } from '@/components/shared/page-header'
import type { Model } from '@/lib/types'

export default function ModelsPage() {
  const { data: models, isLoading } = useModels()
  const createModel = useCreateModel()
  const updateModel = useUpdateModel()
  const deleteModel = useDeleteModel()
  const t = useTranslations('model')
  const tc = useTranslations('common')

  const providers = [
    { value: 'openai', label: t('providers.openai') },
    { value: 'anthropic', label: t('providers.anthropic') },
    { value: 'google', label: t('providers.google') },
    { value: 'custom', label: t('providers.custom') },
  ]

  const [open, setOpen] = useState(false)
  const [editingModel, setEditingModel] = useState<Model | null>(null)
  const [provider, setProvider] = useState('openai')
  const [modelName, setModelName] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')

  function resetForm() {
    setProvider('openai')
    setModelName('')
    setDisplayName('')
    setBaseUrl('')
    setApiKey('')
    setEditingModel(null)
  }

  function openEditDialog(model: Model) {
    setEditingModel(model)
    setProvider(model.provider)
    setModelName(model.model_name)
    setDisplayName(model.display_name)
    setBaseUrl(model.base_url ?? '')
    setApiKey('')
    setOpen(true)
  }

  async function handleSubmit() {
    const payload = {
      provider,
      model_name: modelName,
      display_name: displayName || modelName,
      base_url: baseUrl || undefined,
      api_key: apiKey || undefined,
    }
    if (editingModel) {
      await updateModel.mutateAsync({ id: editingModel.id, data: payload })
    } else {
      await createModel.mutateAsync(payload)
    }
    resetForm()
    setOpen(false)
  }

  function getProviderIcon(p: string) {
    switch (p) {
      case 'openai':
        return 'OAI'
      case 'anthropic':
        return 'ANT'
      case 'google':
        return 'GGL'
      default:
        return 'AI'
    }
  }

  const isSubmitting = editingModel ? updateModel.isPending : createModel.isPending

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader
        title={t('pageTitle')}
        action={
          <Dialog
            open={open}
            onOpenChange={(v) => {
              setOpen(v)
              if (!v) resetForm()
            }}
          >
            <DialogTrigger
              render={
                <Button>
                  <PlusIcon className="size-4" data-icon="inline-start" />
                  {t('addModel')}
                </Button>
              }
            />
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>
                  {editingModel ? t('dialogTitle.edit') : t('dialogTitle.new')}
                </DialogTitle>
                <DialogDescription>
                  {editingModel ? t('dialogDescription.edit') : t('dialogDescription.new')}
                </DialogDescription>
              </DialogHeader>

              <div className="space-y-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">{t('provider')}</label>
                  <Select
                    value={provider}
                    onValueChange={(val) => {
                      if (val) setProvider(val)
                    }}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder={t('providerPlaceholder')} />
                    </SelectTrigger>
                    <SelectContent>
                      {providers.map((p) => (
                        <SelectItem key={p.value} value={p.value}>
                          {p.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    {t('modelName')} <span className="text-destructive">{tc('required')}</span>
                  </label>
                  <Input
                    value={modelName}
                    onChange={(e) => setModelName(e.target.value)}
                    placeholder="gpt-4o"
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">{t('displayName')}</label>
                  <Input
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    placeholder="GPT-4o"
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">{t('baseUrl')}</label>
                  <Input
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder="https://api.openai.com/v1"
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium">{t('apiKey')}</label>
                  <Input
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    type="password"
                    placeholder="sk-xxxxxxxxxxxx"
                  />
                </div>
              </div>

              <DialogFooter>
                <Button onClick={handleSubmit} disabled={!modelName.trim() || isSubmitting}>
                  {isSubmitting && <Loader2Icon className="mr-1 size-4 animate-spin" />}
                  {editingModel ? tc('save') : tc('register')}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        }
      />

      {isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
      ) : models && models.length > 0 ? (
        <div className="space-y-2">
          {models.map((model) => (
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
                    onClick={() => openEditDialog(model)}
                  >
                    <PencilIcon className="size-4 text-muted-foreground" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={t('deleteLabel', { name: model.display_name })}
                    onClick={() => deleteModel.mutate(model.id)}
                    disabled={deleteModel.isPending}
                  >
                    {deleteModel.isPending ? (
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
  )
}
