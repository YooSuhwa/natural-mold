'use client'

import { useState, useMemo } from 'react'
import { Loader2Icon, CheckIcon, DownloadIcon, ArrowLeftIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
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
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select'
import { useDiscoverModels } from '@/lib/hooks/use-providers'
import { useBulkCreateModels, useModels } from '@/lib/hooks/use-models'
import { getProviderIcon, formatContextWindow } from '@/lib/utils/provider'
import type { Provider, DiscoveredModel } from '@/lib/types'

type Step = 'provider' | 'discover' | 'manual'

interface ModelAddDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  providers: Provider[]
}

export function ModelAddDialog({ open, onOpenChange, providers }: ModelAddDialogProps) {
  const t = useTranslations('model')
  const tc = useTranslations('common')

  const discoverModels = useDiscoverModels()
  const bulkCreate = useBulkCreateModels()
  const { data: existingModels } = useModels()

  const [step, setStep] = useState<Step>('provider')
  const [selectedProviderId, setSelectedProviderId] = useState('')
  const [selectedModels, setSelectedModels] = useState<Set<string>>(new Set())
  const [searchQuery, setSearchQuery] = useState('')

  // Manual add fields
  const [manualModelName, setManualModelName] = useState('')
  const [manualDisplayName, setManualDisplayName] = useState('')

  function resetState() {
    setStep('provider')
    setSelectedProviderId('')
    setSelectedModels(new Set())
    setSearchQuery('')
    setManualModelName('')
    setManualDisplayName('')
    discoverModels.reset()
  }

  function handleOpenChange(v: boolean) {
    if (!v) resetState()
    onOpenChange(v)
  }

  function handleDiscover() {
    if (!selectedProviderId) return
    discoverModels.mutate(selectedProviderId, {
      onSuccess: () => setStep('discover'),
    })
  }

  function toggleModel(modelName: string) {
    setSelectedModels((prev) => {
      const next = new Set(prev)
      if (next.has(modelName)) {
        next.delete(modelName)
      } else {
        next.add(modelName)
      }
      return next
    })
  }

  async function handleBulkRegister() {
    try {
      if (!discoverModels.data) return
      const models = discoverModels.data
        .filter((m) => selectedModels.has(m.model_name))
        .map((m) => ({
          model_name: m.model_name,
          display_name: m.display_name,
          context_window: m.context_window,
          max_output_tokens: m.max_output_tokens,
          input_modalities: m.input_modalities,
          output_modalities: m.output_modalities,
          cost_per_input_token: m.cost_per_input_token,
          cost_per_output_token: m.cost_per_output_token,
          supports_vision: m.supports_vision,
          supports_function_calling: m.supports_function_calling,
          supports_reasoning: m.supports_reasoning,
        }))
      await bulkCreate.mutateAsync({ provider_id: selectedProviderId, models })
      handleOpenChange(false)
    } catch {
      toast.error(t('discoverError'))
    }
  }

  async function handleManualAdd() {
    try {
      await bulkCreate.mutateAsync({
        provider_id: selectedProviderId,
        models: [
          { model_name: manualModelName, display_name: manualDisplayName || manualModelName },
        ],
      })
      handleOpenChange(false)
    } catch {
      toast.error(t('discoverError'))
    }
  }

  function isModelRegistered(modelName: string): boolean {
    if (!existingModels) return false
    return existingModels.some(
      (m) => m.model_name === modelName && m.provider_id === selectedProviderId,
    )
  }

  const filteredDiscovered = useMemo(() => {
    if (!discoverModels.data) return []
    const q = searchQuery.toLowerCase()
    return q
      ? discoverModels.data.filter(
          (m) => m.model_name.toLowerCase().includes(q) || m.display_name.toLowerCase().includes(q),
        )
      : discoverModels.data
  }, [discoverModels.data, searchQuery])

  const selectedProvider = providers.find((p) => p.id === selectedProviderId)

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('dialogTitle.new')}</DialogTitle>
          <DialogDescription>{t('dialogDescription.new')}</DialogDescription>
        </DialogHeader>

        {step === 'provider' && (
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">{t('stepProvider')}</label>
              <Select
                value={selectedProviderId}
                onValueChange={(val) => {
                  if (val) setSelectedProviderId(val)
                }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue placeholder={t('selectProvider')} />
                </SelectTrigger>
                <SelectContent>
                  {providers.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      <span className="flex items-center gap-2">
                        <span className="text-xs font-bold text-muted-foreground">
                          {getProviderIcon(p.provider_type)}
                        </span>
                        {p.name}
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <DialogFooter className="flex-col gap-2 sm:flex-col">
              <Button
                onClick={handleDiscover}
                disabled={!selectedProviderId || discoverModels.isPending}
                className="w-full"
              >
                {discoverModels.isPending ? (
                  <Loader2Icon className="mr-1 size-4 animate-spin" />
                ) : (
                  <DownloadIcon className="mr-1 size-4" />
                )}
                {discoverModels.isPending ? t('discovering') : t('discoverModels')}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  if (selectedProviderId) setStep('manual')
                }}
                disabled={!selectedProviderId}
                className="w-full text-muted-foreground"
              >
                {t('manualAdd')}
              </Button>
            </DialogFooter>
          </div>
        )}

        {step === 'discover' && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="icon-sm" onClick={() => setStep('provider')}>
                <ArrowLeftIcon className="size-4" />
              </Button>
              <span className="text-sm font-medium">
                {selectedProvider?.name} — {t('selectModels')}
              </span>
            </div>
            <SearchInput
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t('searchModels')}
            />
            {discoverModels.isError && (
              <p className="text-sm text-destructive">{t('discoverError')}</p>
            )}
            <div className="max-h-[320px] space-y-1 overflow-auto">
              {filteredDiscovered.length === 0 ? (
                <p className="py-4 text-center text-sm text-muted-foreground">
                  {t('discoverEmpty')}
                </p>
              ) : (
                filteredDiscovered.map((model) => {
                  const registered = isModelRegistered(model.model_name)
                  return (
                    <DiscoveredModelRow
                      key={model.model_name}
                      model={model}
                      selected={selectedModels.has(model.model_name)}
                      onToggle={() => toggleModel(model.model_name)}
                      disabled={registered}
                      registeredLabel={registered ? t('alreadyRegistered') : undefined}
                    />
                  )
                })
              )}
            </div>
            <DialogFooter>
              <Button
                onClick={handleBulkRegister}
                disabled={selectedModels.size === 0 || bulkCreate.isPending}
              >
                {bulkCreate.isPending && <Loader2Icon className="mr-1 size-4 animate-spin" />}
                {t('registerSelected', { count: selectedModels.size })}
              </Button>
            </DialogFooter>
          </div>
        )}

        {step === 'manual' && (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="icon-sm" onClick={() => setStep('provider')}>
                <ArrowLeftIcon className="size-4" />
              </Button>
              <span className="text-sm font-medium">
                {selectedProvider?.name} — {t('manualAdd')}
              </span>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {t('modelName')} <span className="text-destructive">{tc('required')}</span>
              </label>
              <Input
                value={manualModelName}
                onChange={(e) => setManualModelName(e.target.value)}
                placeholder="gpt-4o"
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">{t('displayName')}</label>
              <Input
                value={manualDisplayName}
                onChange={(e) => setManualDisplayName(e.target.value)}
                placeholder="GPT-4o"
              />
            </div>
            <DialogFooter>
              <Button
                onClick={handleManualAdd}
                disabled={!manualModelName.trim() || bulkCreate.isPending}
              >
                {bulkCreate.isPending && <Loader2Icon className="mr-1 size-4 animate-spin" />}
                {tc('register')}
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function DiscoveredModelRow({
  model,
  selected,
  onToggle,
  disabled,
  registeredLabel,
}: {
  model: DiscoveredModel
  selected: boolean
  onToggle: () => void
  disabled?: boolean
  registeredLabel?: string
}) {
  return (
    <button
      type="button"
      onClick={disabled ? undefined : onToggle}
      disabled={disabled}
      className={`flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-left text-sm ${
        disabled ? 'opacity-50 cursor-not-allowed' : selected ? 'bg-muted/60' : 'hover:bg-muted/30'
      }`}
    >
      {disabled ? (
        <CheckIcon className="size-3.5 shrink-0 text-muted-foreground" />
      ) : selected ? (
        <CheckIcon className="size-3.5 shrink-0 text-primary" />
      ) : (
        <span className="size-3.5 shrink-0 rounded border border-muted-foreground/30" />
      )}
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
        <span className="mr-1 truncate text-xs font-medium">{model.display_name}</span>
        {registeredLabel && (
          <Badge variant="outline" className="text-[10px]">
            {registeredLabel}
          </Badge>
        )}
        {model.context_window && (
          <Badge variant="outline" className="text-[10px]">
            {formatContextWindow(model.context_window)}
          </Badge>
        )}
        {model.input_modalities?.map((m) => (
          <Badge key={m} variant="secondary" className="text-[10px]">
            {m}
          </Badge>
        ))}
      </div>
    </button>
  )
}
