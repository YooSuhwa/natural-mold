'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { DownloadIcon, ListChecksIcon, Loader2Icon, Trash2Icon } from 'lucide-react'
import { toast } from 'sonner'

import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Button } from '@/components/ui/button'
import { useExportMemories, useMemories } from '@/lib/hooks/use-memory'
import type { MemoryPolicy, MemoryStatusFilter, UserMemorySettings } from '@/lib/types'
import { formatDisplayNumber } from '@/lib/utils/display-format'
import { ApprovalModeChooser } from './approval-mode-chooser'
import { MemoryBulkDeleteFlow } from './memory-bulk-delete'

const COUNTED: ReadonlyArray<Exclude<MemoryStatusFilter, 'all'>> = ['active', 'revoked', 'review']

export function MemoryOverviewTab({
  settings,
  policy,
}: {
  settings: UserMemorySettings
  policy: MemoryPolicy | undefined
}) {
  const t = useTranslations('appSettings.memory')
  return (
    <div className="space-y-4">
      <SettingsSectionCard
        title={
          <span className="flex items-center gap-2">
            <ListChecksIcon className="size-4" aria-hidden />
            {t('mode.title')}
          </span>
        }
        description={t('mode.description')}
      >
        {policy ? (
          <ApprovalModeChooser settings={settings} policy={policy} idPrefix="overview" />
        ) : (
          <p className="text-sm text-muted-foreground">{t('loading')}</p>
        )}
      </SettingsSectionCard>
      <SettingsSectionCard title={t('overview.counts.title')}>
        <dl className="grid gap-3 sm:grid-cols-3">
          {COUNTED.map((status) => (
            <CountTile key={status} status={status} />
          ))}
        </dl>
      </SettingsSectionCard>
      <QuickActions />
    </div>
  )
}

function CountTile({ status }: { status: Exclude<MemoryStatusFilter, 'all'> }) {
  const t = useTranslations('appSettings.memory.overview.counts')
  const { data, isLoading } = useMemories({ status })
  return (
    <div
      className="rounded-lg border border-border/70 bg-background p-3"
      data-testid={`memory-count-${status}`}
    >
      <dt className="text-xs text-muted-foreground">{t(status)}</dt>
      <dd className="mt-1 text-lg font-semibold text-foreground">
        {isLoading ? '-' : formatDisplayNumber(data?.length ?? 0)}
      </dd>
    </div>
  )
}

function QuickActions() {
  const t = useTranslations('appSettings.memory.overview.quick')
  const tExport = useTranslations('appSettings.memory.export')
  const exportMemories = useExportMemories()
  const [deleting, setDeleting] = useState(false)
  const [attempt, setAttempt] = useState(0)

  return (
    <SettingsSectionCard title={t('title')} description={t('description')}>
      <div className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={exportMemories.isPending}
            onClick={() =>
              exportMemories.mutate(
                { format: 'json', query: { scope: 'all' } },
                {
                  onSuccess: () => toast.success(tExport('done')),
                  onError: (error) =>
                    toast.error(error instanceof Error ? error.message : tExport('failed')),
                },
              )
            }
            data-testid="memory-quick-export"
          >
            {exportMemories.isPending ? (
              <Loader2Icon className="size-4 animate-spin" aria-hidden />
            ) : (
              <DownloadIcon className="size-4" aria-hidden />
            )}
            {t('export')}
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={() => {
              setAttempt((n) => n + 1)
              setDeleting(true)
            }}
            data-testid="memory-quick-delete-all"
          >
            <Trash2Icon className="size-4" aria-hidden />
            {t('deleteAll')}
          </Button>
        </div>
        <MemoryBulkDeleteFlow
          key={attempt}
          query={{ scope: 'all' }}
          active={deleting}
          onClose={() => setDeleting(false)}
        />
      </div>
    </SettingsSectionCard>
  )
}
