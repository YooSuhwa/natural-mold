'use client'

import { useState } from 'react'
import { Loader2Icon, CheckCircleIcon, XCircleIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
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
import { toast } from 'sonner'
import { useCreateProvider, useUpdateProvider, useTestProvider } from '@/lib/hooks/use-providers'
import type { Provider, ProviderType } from '@/lib/types'

const PROVIDER_TYPES = [
  { value: 'openai', label: 'OpenAI', defaultUrl: 'https://api.openai.com/v1' },
  { value: 'anthropic', label: 'Anthropic', defaultUrl: '' },
  { value: 'google', label: 'Google (Gemini)', defaultUrl: '' },
  { value: 'openrouter', label: 'OpenRouter', defaultUrl: 'https://openrouter.ai/api/v1' },
  { value: 'openai_compatible', label: 'OpenAI Compatible (Ollama, vLLM)', defaultUrl: '' },
] as const

interface ProviderFormProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  editingProvider: Provider | null
}

export function ProviderForm({ open, onOpenChange, editingProvider }: ProviderFormProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        {open && (
          <ProviderFormContent
            editingProvider={editingProvider}
            onClose={() => onOpenChange(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function ProviderFormContent({
  editingProvider,
  onClose,
}: {
  editingProvider: Provider | null
  onClose: () => void
}) {
  const t = useTranslations('provider')
  const tc = useTranslations('common')

  const createProvider = useCreateProvider()
  const updateProvider = useUpdateProvider()
  const testProvider = useTestProvider()

  const [providerType, setProviderType] = useState(editingProvider?.provider_type ?? 'openai')
  const [name, setName] = useState(editingProvider?.name ?? 'OpenAI')
  const [baseUrl, setBaseUrl] = useState(editingProvider?.base_url ?? 'https://api.openai.com/v1')
  const [apiKey, setApiKey] = useState('')

  function handleTypeChange(type: ProviderType) {
    setProviderType(type)
    const pt = PROVIDER_TYPES.find((p) => p.value === type)
    if (pt && !editingProvider) {
      setName(pt.label)
      setBaseUrl(pt.defaultUrl)
    }
  }

  async function handleSubmit() {
    try {
      if (editingProvider) {
        await updateProvider.mutateAsync({
          id: editingProvider.id,
          data: {
            name: name || undefined,
            base_url: baseUrl || undefined,
            api_key: apiKey || undefined,
          },
        })
      } else {
        await createProvider.mutateAsync({
          name,
          provider_type: providerType,
          base_url: baseUrl || undefined,
          api_key: apiKey || undefined,
        })
      }
      onClose()
    } catch {
      toast.error(editingProvider ? t('updateError') : t('createError'))
    }
  }

  function handleTest() {
    if (editingProvider) {
      testProvider.mutate(editingProvider.id)
    }
  }

  const isSubmitting = editingProvider ? updateProvider.isPending : createProvider.isPending
  const isBaseUrlRequired = providerType === 'openai_compatible'

  return (
    <>
      <DialogHeader>
        <DialogTitle>{editingProvider ? t('editProvider') : t('addProvider')}</DialogTitle>
        <DialogDescription>
          {editingProvider ? t('dialogDescription.edit') : t('dialogDescription.new')}
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-4">
        {!editingProvider && (
          <div className="space-y-2">
            <label htmlFor="provider-type" className="text-sm font-medium">
              {t('providerType')}
            </label>
            <Select
              value={providerType}
              onValueChange={(val) => {
                if (val) handleTypeChange(val as ProviderType)
              }}
            >
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PROVIDER_TYPES.map((pt) => (
                  <SelectItem key={pt.value} value={pt.value}>
                    {pt.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <div className="space-y-2">
          <label htmlFor="provider-name" className="text-sm font-medium">
            {t('name')}
          </label>
          <Input
            id="provider-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="My Provider"
          />
        </div>

        {(isBaseUrlRequired || baseUrl) && (
          <div className="space-y-2">
            <label htmlFor="provider-base-url" className="text-sm font-medium">
              {t('baseUrl')}
              {isBaseUrlRequired && <span className="text-destructive"> {tc('required')}</span>}
            </label>
            <Input
              id="provider-base-url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://localhost:11434/v1"
            />
          </div>
        )}

        <div className="space-y-2">
          <label htmlFor="provider-api-key" className="text-sm font-medium">
            {t('apiKey')}
          </label>
          <Input
            id="provider-api-key"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            type="password"
            placeholder="sk-xxxxxxxxxxxx"
          />
        </div>

        {editingProvider && (
          <div className="space-y-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleTest}
              disabled={testProvider.isPending}
              className="w-full"
            >
              {testProvider.isPending && <Loader2Icon className="mr-1 size-4 animate-spin" />}
              {t('testConnection')}
            </Button>
            {testProvider.isSuccess && (
              <div className="flex items-center gap-2 rounded-md bg-green-50 p-2 text-sm text-green-700 dark:bg-green-950 dark:text-green-300">
                <CheckCircleIcon className="size-4" />
                {testProvider.data.message}
              </div>
            )}
            {testProvider.isError && (
              <div className="flex items-center gap-2 rounded-md bg-red-50 p-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300">
                <XCircleIcon className="size-4" />
                {t('testFailed')}
              </div>
            )}
          </div>
        )}
      </div>

      <DialogFooter>
        <Button
          onClick={handleSubmit}
          disabled={!name.trim() || (isBaseUrlRequired && !baseUrl.trim()) || isSubmitting}
        >
          {isSubmitting && <Loader2Icon className="mr-1 size-4 animate-spin" />}
          {editingProvider ? tc('save') : tc('register')}
        </Button>
      </DialogFooter>
    </>
  )
}
