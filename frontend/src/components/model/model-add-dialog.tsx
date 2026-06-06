'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Loader2, Zap } from 'lucide-react'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ModelDiscoverPanel } from './model-discover-panel'
import { ModelConnectionTest } from './model-connection-test'
import { perMillionToTokenPrice } from './model-format'
import { useCreateModel } from '@/lib/hooks/use-models'
import { useCredentials, useCredentialTypes } from '@/lib/hooks/use-credentials'

interface ModelAddDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

const PROVIDERS = [
  { value: 'openai', labelKey: 'openai' },
  { value: 'anthropic', labelKey: 'anthropic' },
  { value: 'google', labelKey: 'google' },
  { value: 'openrouter', labelKey: 'openrouter' },
  { value: 'openai_compatible', labelKey: 'openai_compatible' },
  { value: 'azure_openai', labelKey: 'azure_openai' },
  { value: 'other', labelKey: 'other' },
]

/**
 * Resource-locator pattern: two ways to add a model.
 *
 * 1. **Discover** — pick a saved LLM credential, the backend lists models
 *    reachable through it (with pricing/source enrichment), tick the ones
 *    to register in bulk.
 * 2. **Custom ID** — type provider + model_name + (optional) pricing for
 *    private/preview models the discovery endpoints don't surface yet.
 */
export function ModelAddDialog({ open, onOpenChange }: ModelAddDialogProps) {
  const t = useTranslations('model.addDialog')
  const [tab, setTab] = useState<'discover' | 'custom'>('discover')

  return (
    <DialogShell open={open} onOpenChange={onOpenChange} size="lg" height="fixed">
      <DialogShell.Header title={t('title')} description={t('description')} />
      <DialogShell.Body className="flex flex-col">
        <Tabs
          value={tab}
          onValueChange={(v) => setTab(v as 'discover' | 'custom')}
          className="flex min-h-0 w-full flex-1 flex-col"
        >
          <TabsList className="w-full">
            <TabsTrigger value="discover">{t('tabs.discover')}</TabsTrigger>
            <TabsTrigger value="custom">{t('tabs.custom')}</TabsTrigger>
          </TabsList>

          <TabsContent value="discover" className="min-w-0 flex-1 overflow-y-auto pt-4">
            <ModelDiscoverPanel onComplete={() => onOpenChange(false)} />
          </TabsContent>

          <TabsContent value="custom" className="min-w-0 flex-1 overflow-y-auto pt-4">
            <CustomIdForm onSaved={() => onOpenChange(false)} />
          </TabsContent>
        </Tabs>
      </DialogShell.Body>
    </DialogShell>
  )
}

function CustomIdForm({ onSaved }: { onSaved: () => void }) {
  const t = useTranslations('model.addDialog')
  const create = useCreateModel()
  const { data: credentials } = useCredentials()
  const { data: definitions } = useCredentialTypes()
  const [provider, setProvider] = useState('openai')
  const [modelName, setModelName] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [inputPriceM, setInputPriceM] = useState('')
  const [outputPriceM, setOutputPriceM] = useState('')
  const [contextWindow, setContextWindow] = useState('')
  const [testCredId, setTestCredId] = useState<string>('')
  const [testOpen, setTestOpen] = useState(false)

  const canSubmit = provider.trim() !== '' && modelName.trim() !== ''

  // Filter to LLM credentials so the test panel only offers API keys.
  const llmCredentials = useMemo(() => {
    if (!credentials || !definitions) return []
    const llmKeys = new Set(definitions.filter((d) => d.category === 'llm').map((d) => d.key))
    return credentials.filter((c) => llmKeys.has(c.definition_key))
  }, [credentials, definitions])

  // Default to the first LLM credential — minimizes clicks for the common case.
  const effectiveCredId = testCredId || llmCredentials[0]?.id || ''

  async function handleSave() {
    if (!canSubmit) return
    try {
      await create.mutateAsync({
        provider: provider.trim(),
        model_name: modelName.trim(),
        display_name: displayName.trim() || modelName.trim(),
        base_url: baseUrl.trim() || null,
        cost_per_input_token: perMillionToTokenPrice(inputPriceM),
        cost_per_output_token: perMillionToTokenPrice(outputPriceM),
        context_window: contextWindow ? Number(contextWindow) : null,
        source: 'manual',
        // The credential the user chose for the test panel becomes the
        // model's default credential — same intent as the Discover flow.
        default_credential_id: effectiveCredId || null,
      })
      toast.success(t('toast.added'))
      onSaved()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('toast.saveFailed'))
    }
  }

  return (
    <div className="space-y-4">
      <p className="rounded border bg-muted/40 p-3 text-xs text-muted-foreground">
        {t('customHint')}
      </p>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <label htmlFor="m-provider" className="text-xs font-medium">
            {t('fields.provider')}
            <span className="ml-0.5 text-destructive">*</span>
          </label>
          <Select value={provider} onValueChange={(v) => v && setProvider(v)}>
            <SelectTrigger id="m-provider" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PROVIDERS.map((p) => (
                <SelectItem key={p.value} value={p.value}>
                  {t(`providers.${p.labelKey}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <label htmlFor="m-model-name" className="text-xs font-medium">
            {t('fields.modelId')}
            <span className="ml-0.5 text-destructive">*</span>
          </label>
          <Input
            id="m-model-name"
            value={modelName}
            placeholder={t('placeholders.modelId')}
            onChange={(e) => setModelName(e.target.value)}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <label htmlFor="m-display-name" className="text-xs font-medium">
          {t('fields.displayName')}
        </label>
        <Input
          id="m-display-name"
          value={displayName}
          placeholder={t('placeholders.displayName')}
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </div>

      <div className="space-y-1.5">
        <label htmlFor="m-base-url" className="text-xs font-medium">
          {t('fields.baseUrl')}
        </label>
        <Input
          id="m-base-url"
          value={baseUrl}
          placeholder={t('placeholders.baseUrl')}
          onChange={(e) => setBaseUrl(e.target.value)}
        />
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1.5">
          <label htmlFor="m-input-price" className="text-xs font-medium">
            {t('fields.inputPrice')}
          </label>
          <Input
            id="m-input-price"
            type="number"
            step="0.01"
            min="0"
            value={inputPriceM}
            placeholder="0"
            onChange={(e) => setInputPriceM(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="m-output-price" className="text-xs font-medium">
            {t('fields.outputPrice')}
          </label>
          <Input
            id="m-output-price"
            type="number"
            step="0.01"
            min="0"
            value={outputPriceM}
            placeholder="0"
            onChange={(e) => setOutputPriceM(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <label htmlFor="m-context" className="text-xs font-medium">
            {t('fields.contextWindow')}
          </label>
          <Input
            id="m-context"
            type="number"
            step="1024"
            min="0"
            value={contextWindow}
            placeholder="128000"
            onChange={(e) => setContextWindow(e.target.value)}
          />
        </div>
      </div>

      {testOpen && effectiveCredId && (
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <label className="text-xs font-medium" htmlFor="custom-test-cred">
              {t('fields.testCredential')}
            </label>
            <Button variant="ghost" size="sm" onClick={() => setTestOpen(false)}>
              {t('actions.hideTest')}
            </Button>
          </div>
          <Select value={effectiveCredId} onValueChange={(v) => v && setTestCredId(v)}>
            <SelectTrigger id="custom-test-cred" className="w-full">
              <SelectValue placeholder={t('placeholders.selectCredential')} />
            </SelectTrigger>
            <SelectContent>
              {llmCredentials.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <ModelConnectionTest
            key={`${provider}-${modelName}-${effectiveCredId}`}
            mode="preview"
            provider={provider.trim()}
            modelName={modelName.trim()}
            baseUrl={baseUrl.trim() || null}
            credentialId={effectiveCredId}
            modelLabel={displayName.trim() || modelName.trim()}
            autoStart
          />
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button
          variant="outline"
          onClick={() => setTestOpen(true)}
          disabled={!canSubmit || llmCredentials.length === 0}
          data-testid="custom-test-button"
        >
          <Zap className="size-3.5" />
          {t('actions.test')}
        </Button>
        <Button onClick={handleSave} disabled={!canSubmit || create.isPending}>
          {create.isPending && <Loader2 className="size-4 animate-spin" />}
          {t('actions.save')}
        </Button>
      </div>
    </div>
  )
}
