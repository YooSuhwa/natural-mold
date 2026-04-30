'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { CpuIcon, SettingsIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useModels } from '@/lib/hooks/use-models'
import { ModelDialog } from '../dialogs/model-dialog'

interface SectionModelProps {
  modelId: string
  onModelIdChange: (v: string) => void
  temperature: number
  onTemperatureChange: (v: number) => void
  topP: number
  onTopPChange: (v: number) => void
  maxTokens: number
  onMaxTokensChange: (v: number) => void
  onReset: () => void
  fallbackIds?: string[]
  onFallbackIdsChange?: (ids: string[]) => void
}

export function SectionModel(props: SectionModelProps) {
  const t = useTranslations('agent.settings')
  const [open, setOpen] = useState(false)
  const { data: models } = useModels()

  const current = models?.find((m) => m.id === props.modelId)
  const summary = current?.display_name ?? t('modelPlaceholder')
  const fallbackCount = props.fallbackIds?.length ?? 0

  return (
    <>
      <div className="flex items-center justify-between rounded-lg border px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <CpuIcon className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">{t('model')}</span>
          <span className="truncate text-sm text-muted-foreground">{summary}</span>
          {fallbackCount > 0 && (
            <span
              className="rounded-md border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:border-amber-900/40 dark:bg-amber-950/40 dark:text-amber-300"
              data-testid="section-model-fallback-badge"
            >
              +{fallbackCount} fallback
            </span>
          )}
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => setOpen(true)}
          aria-label={t('modelConfig')}
        >
          <SettingsIcon className="size-4" />
        </Button>
      </div>
      <ModelDialog open={open} onOpenChange={setOpen} {...props} />
    </>
  )
}
