'use client'

import { useState } from 'react'
import { toast } from 'sonner'
import { Loader2 } from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
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
import { perMillionToTokenPrice } from './model-format'
import { useCreateModel } from '@/lib/hooks/use-models'

interface ModelAddDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

const PROVIDERS = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'google', label: 'Google' },
  { value: 'openrouter', label: 'OpenRouter' },
  { value: 'openai_compatible', label: 'OpenAI Compatible' },
  { value: 'azure_openai', label: 'Azure OpenAI' },
  { value: 'other', label: 'Other' },
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
  const [tab, setTab] = useState<'discover' | 'custom'>('discover')

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Add model</DialogTitle>
          <DialogDescription>
            Discover models from a credential, or enter a custom ID for
            previews and private deployments.
          </DialogDescription>
        </DialogHeader>

        <Tabs
          value={tab}
          onValueChange={(v) => setTab(v as 'discover' | 'custom')}
          className="w-full"
        >
          <TabsList className="w-full">
            <TabsTrigger value="discover">Discover</TabsTrigger>
            <TabsTrigger value="custom">Custom ID</TabsTrigger>
          </TabsList>

          <TabsContent value="discover" className="pt-4">
            <ModelDiscoverPanel onComplete={() => onOpenChange(false)} />
          </TabsContent>

          <TabsContent value="custom" className="pt-4">
            <CustomIdForm onSaved={() => onOpenChange(false)} />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

function CustomIdForm({ onSaved }: { onSaved: () => void }) {
  const create = useCreateModel()
  const [provider, setProvider] = useState('openai')
  const [modelName, setModelName] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [inputPriceM, setInputPriceM] = useState('')
  const [outputPriceM, setOutputPriceM] = useState('')
  const [contextWindow, setContextWindow] = useState('')

  const canSubmit = provider.trim() !== '' && modelName.trim() !== ''

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
      })
      toast.success('Model added')
      onSaved()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed')
    }
  }

  return (
    <div className="space-y-4">
      <p className="rounded border bg-muted/40 p-3 text-xs text-muted-foreground">
        Use this for new or private models that don&apos;t show up in
        discovery. Without explicit pricing, cost tracking will be inaccurate.
      </p>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <label htmlFor="m-provider" className="text-xs font-medium">
            Provider
            <span className="ml-0.5 text-destructive">*</span>
          </label>
          <Select value={provider} onValueChange={(v) => v && setProvider(v)}>
            <SelectTrigger id="m-provider" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PROVIDERS.map((p) => (
                <SelectItem key={p.value} value={p.value}>
                  {p.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-1.5">
          <label htmlFor="m-model-name" className="text-xs font-medium">
            Model ID
            <span className="ml-0.5 text-destructive">*</span>
          </label>
          <Input
            id="m-model-name"
            value={modelName}
            placeholder="gpt-5-preview"
            onChange={(e) => setModelName(e.target.value)}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <label htmlFor="m-display-name" className="text-xs font-medium">
          Display name
        </label>
        <Input
          id="m-display-name"
          value={displayName}
          placeholder="GPT-5 Preview (defaults to model ID)"
          onChange={(e) => setDisplayName(e.target.value)}
        />
      </div>

      <div className="space-y-1.5">
        <label htmlFor="m-base-url" className="text-xs font-medium">
          Base URL
        </label>
        <Input
          id="m-base-url"
          value={baseUrl}
          placeholder="https://api.example.com/v1"
          onChange={(e) => setBaseUrl(e.target.value)}
        />
      </div>

      <div className="grid grid-cols-3 gap-3">
        <div className="space-y-1.5">
          <label htmlFor="m-input-price" className="text-xs font-medium">
            Input $/1M
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
            Output $/1M
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
            Context window
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

      <DialogFooter>
        <Button onClick={handleSave} disabled={!canSubmit || create.isPending}>
          {create.isPending && <Loader2 className="size-4 animate-spin" />}
          Save model
        </Button>
      </DialogFooter>
    </div>
  )
}
