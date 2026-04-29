'use client'

import { useMemo, useState } from 'react'
import { Pencil } from 'lucide-react'

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
import type { ModelPick } from '@/lib/types/model'

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
  /** Enables the Custom-ID toggle (resourceLocator id mode — see NOTICES.md). */
  allowCustomId?: boolean
  className?: string
  placeholder?: string
}

/**
 * Two-mode model picker (resourceLocator pattern — see NOTICES.md).
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
  const { data: models, isLoading } = useModels()
  const [mode, setMode] = useState<'list' | 'custom'>('list')
  const [customProvider, setCustomProvider] = useState('openai')
  const [customModelName, setCustomModelName] = useState('')

  const sortedModels = useMemo(() => {
    if (!models) return []
    return [...models].sort((a, b) => {
      if (a.is_default !== b.is_default) return a.is_default ? -1 : 1
      return a.display_name.localeCompare(b.display_name)
    })
  }, [models])

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
    return (
      <div className={className}>
        <p className="mb-2 text-[11px] text-muted-foreground">
          Custom IDs skip catalog pricing — register frequently used models on
          the Models page for accurate cost tracking.
        </p>
        <div className="flex gap-2">
          <Input
            value={customProvider}
            onChange={(e) => setCustomProvider(e.target.value)}
            onBlur={() => emitCustom(customProvider, customModelName)}
            placeholder="provider"
            className="w-32"
            aria-label="Custom provider"
          />
          <Input
            value={customModelName}
            onChange={(e) => setCustomModelName(e.target.value)}
            onBlur={() => emitCustom(customProvider, customModelName)}
            placeholder="model id"
            className="flex-1"
            aria-label="Custom model id"
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setMode('list')}
          >
            From list
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className={className}>
      <div className="flex items-center gap-2">
        <Select
          value={value}
          onValueChange={(v) => v && emitList(v)}
          disabled={isLoading}
        >
          <SelectTrigger className="flex-1">
            <SelectValue placeholder={placeholder} />
          </SelectTrigger>
          <SelectContent>
            {sortedModels.map((m) => (
              <SelectItem key={m.id} value={m.id}>
                <span className="flex items-center gap-2">
                  <span>{m.display_name}</span>
                  <span className="text-[10px] text-muted-foreground">
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
            aria-label="Use a custom model id"
          >
            <Pencil className="size-3.5" />
            Custom
          </Button>
        )}
      </div>
    </div>
  )
}
