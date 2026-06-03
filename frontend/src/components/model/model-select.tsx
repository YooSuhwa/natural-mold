'use client'

import { useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Pencil, Zap } from 'lucide-react'

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { useModels } from '@/lib/hooks/use-models'
import { useCredentials, useCredentialTypes } from '@/lib/hooks/use-credentials'
import { ModelConnectionTest } from './model-connection-test'
import type { ModelPick, ModelTestResponse } from '@/lib/types/model'

interface ModelSelectProps {
  /** Current model id (List mode). */
  value?: string
  /** Back-compat: id-only handler. Kept so existing dialogs need no migration. */
  onValueChange?: (id: string) => void
  /**
   * Resource-locator handler. When supplied, the component emits either a
   * List pick or a Custom-ID pick. Callers that opt in must persist both
   * shapes themselves.
   */
  onChange?: (pick: ModelPick) => void
  /** Enables the Custom-ID toggle. */
  allowCustomId?: boolean
  className?: string
  placeholder?: string
}

/**
 * Two-mode model picker.
 *
 * - **List** (default): shows the catalog from `/api/models`, returns a
 *   `model_id`. Existing callers using `onValueChange` keep working.
 * - **Custom ID** (when `allowCustomId={true}`): provider + model_name typed
 *   directly. Not persisted to DB; the parent decides how to encode it.
 */
export function ModelSelect({
  value,
  onValueChange,
  onChange,
  allowCustomId = false,
  className,
  placeholder = 'Select a model',
}: ModelSelectProps) {
  const t = useTranslations('model.select')
  const { data: models, isLoading } = useModels()
  const { data: credentials } = useCredentials()
  const { data: definitions } = useCredentialTypes()
  const [mode, setMode] = useState<'list' | 'custom'>('list')
  const [customProvider, setCustomProvider] = useState('openai')
  const [customModelName, setCustomModelName] = useState('')
  const [customTestCredId, setCustomTestCredId] = useState<string>('')
  const [showTest, setShowTest] = useState(false)
  const [lastTestResult, setLastTestResult] = useState<ModelTestResponse | null>(
    null,
  )

  const sortedModels = useMemo(() => {
    if (!models) return []
    return [...models].sort((a, b) => {
      if (a.is_default !== b.is_default) return a.is_default ? -1 : 1
      return a.display_name.localeCompare(b.display_name)
    })
  }, [models])

  const llmCredentials = useMemo(() => {
    if (!credentials || !definitions) return []
    const llmKeys = new Set(
      definitions.filter((d) => d.category === 'llm').map((d) => d.key),
    )
    return credentials.filter((c) => llmKeys.has(c.definition_key))
  }, [credentials, definitions])

  const effectiveCustomCredId =
    customTestCredId || llmCredentials[0]?.id || ''

  function emitList(id: string) {
    onValueChange?.(id)
    onChange?.({ mode: 'list', model_id: id })
  }

  function emitCustom(provider: string, modelName: string) {
    if (!modelName.trim()) return
    onChange?.({
      mode: 'custom',
      provider: provider.trim(),
      model_name: modelName.trim(),
    })
  }

  if (mode === 'custom') {
    const canTest =
      customProvider.trim() !== '' &&
      customModelName.trim() !== '' &&
      effectiveCustomCredId !== ''

    return (
      <div className={className}>
        <p className="mb-2 moldy-ui-caption text-muted-foreground">
          {t('customHint')}
        </p>
        <div className="flex gap-2">
          <Input
            value={customProvider}
            onChange={(e) => setCustomProvider(e.target.value)}
            onBlur={() => emitCustom(customProvider, customModelName)}
            placeholder={t('providerPlaceholder')}
            className="w-32"
            aria-label={t('providerLabel')}
          />
          <Input
            value={customModelName}
            onChange={(e) => setCustomModelName(e.target.value)}
            onBlur={() => emitCustom(customProvider, customModelName)}
            placeholder={t('modelPlaceholder')}
            className="flex-1"
            aria-label={t('modelLabel')}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setShowTest((v) => !v)}
            disabled={!canTest}
            data-testid="model-select-test"
          >
            <Zap className="size-3.5" /> {t('test')}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setMode('list')}
          >
            {t('fromList')}
          </Button>
        </div>

        {showTest && canTest && (
          <div className="mt-2 space-y-2">
            <Select
              value={effectiveCustomCredId}
              onValueChange={(v) => v && setCustomTestCredId(v)}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder={t('selectCredential')} />
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
              key={`${customProvider}-${customModelName}-${effectiveCustomCredId}`}
              mode="preview"
              provider={customProvider.trim()}
              modelName={customModelName.trim()}
              credentialId={effectiveCustomCredId}
              modelLabel={customModelName.trim()}
              autoStart
              onComplete={(r) => {
                setLastTestResult(r)
                if (r.success) {
                  // Test passed → emit immediately so the parent can rely on it.
                  emitCustom(customProvider, customModelName)
                }
              }}
            />
            {lastTestResult && !lastTestResult.success && (
              <p className="moldy-ui-caption text-destructive">
                {t('testFailed')}
              </p>
            )}
          </div>
        )}
      </div>
    )
  }

  // base-ui's <Select.Value> renders the raw value (UUID) when no children
  // are provided, so we look up the picked model and project a friendly label.
  const selectedModel = sortedModels.find((m) => m.id === value)

  return (
    <div className={className}>
      <div className="flex items-center gap-2">
        <Select
          value={value}
          onValueChange={(v) => v && emitList(v)}
          disabled={isLoading}
        >
          <SelectTrigger className="flex-1">
            <SelectValue placeholder={placeholder}>
              {selectedModel ? (
                <span className="flex items-center gap-2">
                  <span>{selectedModel.display_name}</span>
                  <span className="moldy-ui-micro text-muted-foreground">
                    {selectedModel.provider}
                  </span>
                </span>
              ) : null}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {sortedModels.map((m) => (
              <SelectItem key={m.id} value={m.id}>
                <span className="flex items-center gap-2">
                  <span>{m.display_name}</span>
                  <span className="moldy-ui-micro text-muted-foreground">
                    {m.provider}
                  </span>
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {allowCustomId && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setMode('custom')}
            aria-label={t('useCustom')}
          >
            <Pencil className="size-3.5" />
            {t('custom')}
          </Button>
        )}
      </div>
    </div>
  )
}
