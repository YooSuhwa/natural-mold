'use client'

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'
import { ArrowDownIcon, ArrowUpIcon, PlusIcon, Trash2Icon } from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
  DialogClose,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Slider } from '@/components/ui/slider'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ModelSelect } from '@/components/model/model-select'
import { useModels } from '@/lib/hooks/use-models'

const MAX_FALLBACKS = 5

interface ModelDialogProps {
  open: boolean
  onOpenChange: (v: boolean) => void
  modelId: string
  onModelIdChange: (v: string) => void
  temperature: number
  onTemperatureChange: (v: number) => void
  topP: number
  onTopPChange: (v: number) => void
  maxTokens: number
  onMaxTokensChange: (v: number) => void
  onReset: () => void
  /**
   * M10 — fallback model UUIDs tried in order when the primary model fails.
   * `undefined` means callers haven't migrated yet; treat as empty list.
   */
  fallbackIds?: string[]
  onFallbackIdsChange?: (ids: string[]) => void
}

export function ModelDialog({
  open,
  onOpenChange,
  modelId,
  onModelIdChange,
  temperature,
  onTemperatureChange,
  topP,
  onTopPChange,
  maxTokens,
  onMaxTokensChange,
  onReset,
  fallbackIds = [],
  onFallbackIdsChange,
}: ModelDialogProps) {
  const t = useTranslations('agent.settings')
  const tu = useTranslations('usage.fallback')
  const tc = useTranslations('common')

  const { data: models } = useModels()

  // Models eligible to be picked as a fallback. Exclude the primary; allowing
  // it would be a no-op chain element. We do NOT exclude already-selected
  // fallbacks here so the per-row Select can still display the current value;
  // the duplicate guard below warns the user instead.
  const candidateModels = useMemo(() => {
    if (!models) return []
    return models.filter((m) => m.id !== modelId)
  }, [models, modelId])

  const usedSet = useMemo(() => new Set([modelId, ...fallbackIds]), [modelId, fallbackIds])

  const fallbackEnabled = !!onFallbackIdsChange

  const handleAdd = () => {
    if (!onFallbackIdsChange) return
    if (fallbackIds.length >= MAX_FALLBACKS) return
    // Pick the first model that is not already used in the chain.
    const next = candidateModels.find((m) => !usedSet.has(m.id))
    if (!next) return
    onFallbackIdsChange([...fallbackIds, next.id])
  }

  const handleChangeAt = (index: number, value: string) => {
    if (!onFallbackIdsChange) return
    const next = [...fallbackIds]
    next[index] = value
    onFallbackIdsChange(next)
  }

  const handleRemoveAt = (index: number) => {
    if (!onFallbackIdsChange) return
    onFallbackIdsChange(fallbackIds.filter((_, i) => i !== index))
  }

  const handleMove = (index: number, direction: -1 | 1) => {
    if (!onFallbackIdsChange) return
    const target = index + direction
    if (target < 0 || target >= fallbackIds.length) return
    const next = [...fallbackIds]
    const [moved] = next.splice(index, 1)
    next.splice(target, 0, moved)
    onFallbackIdsChange(next)
  }

  // Duplicate detection — flag any fallback that equals the primary or another
  // fallback. The user can still save (warning is informational), but they get
  // an explicit signal that the chain has dead links.
  const duplicateIndexes = useMemo(() => {
    const seen = new Set<string>([modelId])
    const dups = new Set<number>()
    fallbackIds.forEach((id, i) => {
      if (seen.has(id)) dups.add(i)
      seen.add(id)
    })
    return dups
  }, [modelId, fallbackIds])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('modelDialogTitle')}</DialogTitle>
        </DialogHeader>

        <div className="space-y-6 py-2">
          {/* Model Select */}
          <div className="space-y-2">
            <label className="text-sm font-medium">{t('model')}</label>
            <ModelSelect
              value={modelId}
              onValueChange={onModelIdChange}
              allowCustomId
              className="rounded-lg border p-2"
            />
          </div>

          {/* Model Parameters */}
          <div className="space-y-5 rounded-lg border p-4">
            <label className="text-sm font-medium">{t('modelParams')}</label>

            {/* Temperature */}
            <div className="space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">{t('temperatureLabel')}</span>
                <span className="font-mono text-xs tabular-nums">{temperature.toFixed(1)}</span>
              </div>
              <Slider
                value={[temperature]}
                onValueChange={(val) =>
                  onTemperatureChange(Array.isArray(val) ? val[0] : (val as number))
                }
                min={0}
                max={2}
                step={0.1}
              />
              <div className="flex justify-between text-[10px] text-muted-foreground">
                <span>{t('temperature.accurate')}</span>
                <span>{t('temperature.creative')}</span>
              </div>
            </div>

            {/* Top P */}
            <div className="space-y-3">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">{t('topPLabel')}</span>
                <span className="font-mono text-xs tabular-nums">{topP.toFixed(1)}</span>
              </div>
              <Slider
                value={[topP]}
                onValueChange={(val) =>
                  onTopPChange(Array.isArray(val) ? val[0] : (val as number))
                }
                min={0}
                max={1}
                step={0.1}
              />
            </div>

            {/* Max Tokens */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted-foreground">{t('maxTokensLabel')}</span>
              </div>
              <Input
                type="number"
                min="256"
                max="32768"
                step="256"
                value={maxTokens}
                onChange={(e) => onMaxTokensChange(Number(e.target.value) || 4096)}
              />
            </div>

            <Button
              variant="ghost"
              size="sm"
              className="text-xs text-muted-foreground"
              onClick={onReset}
            >
              {t('resetToDefault')}
            </Button>
          </div>

          {fallbackEnabled && (
            <FallbackSection
              fallbackIds={fallbackIds}
              candidateModels={candidateModels}
              duplicateIndexes={duplicateIndexes}
              t={tu}
              onAdd={handleAdd}
              onChangeAt={handleChangeAt}
              onRemoveAt={handleRemoveAt}
              onMove={handleMove}
            />
          )}
        </div>

        <DialogFooter>
          <DialogClose render={<Button>{tc('done')}</Button>} />
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

type FallbackTranslator = ReturnType<typeof useTranslations<'usage.fallback'>>

interface FallbackSectionProps {
  fallbackIds: string[]
  candidateModels: { id: string; display_name: string; provider: string }[]
  duplicateIndexes: Set<number>
  t: FallbackTranslator
  onAdd: () => void
  onChangeAt: (index: number, value: string) => void
  onRemoveAt: (index: number) => void
  onMove: (index: number, direction: -1 | 1) => void
}

function FallbackSection({
  fallbackIds,
  candidateModels,
  duplicateIndexes,
  t,
  onAdd,
  onChangeAt,
  onRemoveAt,
  onMove,
}: FallbackSectionProps) {
  return (
    <details
      className="rounded-lg border p-4 [&[open]]:bg-muted/10"
      data-testid="fallback-section"
      open={fallbackIds.length > 0}
    >
      <summary className="cursor-pointer text-sm font-medium">
        {t('sectionTitle')}
        {fallbackIds.length > 0 && (
          <span className="ml-2 text-xs text-muted-foreground">
            ({fallbackIds.length})
          </span>
        )}
      </summary>
      <div className="mt-3 space-y-3">
        <p className="text-xs text-muted-foreground">{t('sectionHint')}</p>

        {fallbackIds.length === 0 && (
          <p className="rounded-md border border-dashed bg-muted/20 p-3 text-center text-xs text-muted-foreground">
            {t('limitHint', { max: MAX_FALLBACKS })}
          </p>
        )}

        <ul className="space-y-2">
          {fallbackIds.map((id, index) => {
            const isDup = duplicateIndexes.has(index)
            return (
              <li
                key={`${id}-${index}`}
                className="space-y-1.5"
                data-testid={`fallback-row-${index}`}
              >
                <div className="flex items-center gap-2">
                  <span className="w-5 text-center font-mono text-[10px] text-muted-foreground">
                    {index + 1}
                  </span>
                  <Select value={id} onValueChange={(v) => v && onChangeAt(index, v)}>
                    <SelectTrigger
                      className="flex-1"
                      data-testid={`fallback-select-${index}`}
                    >
                      <SelectValue>
                        {(selected) =>
                          candidateModels.find((m) => m.id === selected)
                            ?.display_name ?? ''
                        }
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {candidateModels.map((m) => (
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
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label={t('moveUp')}
                    onClick={() => onMove(index, -1)}
                    disabled={index === 0}
                  >
                    <ArrowUpIcon className="size-3.5" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label={t('moveDown')}
                    onClick={() => onMove(index, 1)}
                    disabled={index === fallbackIds.length - 1}
                  >
                    <ArrowDownIcon className="size-3.5" />
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label={t('remove')}
                    onClick={() => onRemoveAt(index)}
                  >
                    <Trash2Icon className="size-3.5 text-destructive" />
                  </Button>
                </div>
                {isDup && (
                  <p
                    className="ml-7 text-[11px] text-amber-600"
                    data-testid={`fallback-duplicate-${index}`}
                  >
                    {t('duplicateWarning')}
                  </p>
                )}
              </li>
            )
          })}
        </ul>

        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={onAdd}
          disabled={
            fallbackIds.length >= MAX_FALLBACKS || candidateModels.length === 0
          }
          data-testid="fallback-add-button"
        >
          <PlusIcon className="size-3.5" />
          {t('addButton')}
        </Button>
      </div>
    </details>
  )
}
