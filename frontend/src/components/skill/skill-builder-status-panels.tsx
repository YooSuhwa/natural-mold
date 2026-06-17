'use client'

import Link from 'next/link'
import { useTranslations } from 'next-intl'
import { AlertTriangle, Loader2, Sparkles } from 'lucide-react'

import { Button } from '@/components/ui/button'

export function ImprovementConflict({
  reloadPending,
  onReloadLatest,
  onDiscard,
}: {
  readonly reloadPending: boolean
  readonly onReloadLatest: () => void
  readonly onDiscard: () => void
}) {
  const t = useTranslations('skill.builderDialog.conflict')
  return (
    <div className="moldy-status-surface moldy-status-warn flex flex-col gap-3 rounded-md p-3">
      <div className="flex items-start gap-3">
        <AlertTriangle className="moldy-status-icon mt-0.5 size-4 shrink-0" />
        <div>
          <p className="moldy-status-text text-sm font-semibold">{t('title')}</p>
          <p className="moldy-status-muted-text mt-1 text-xs">{t('description')}</p>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" onClick={onReloadLatest} disabled={reloadPending}>
          {reloadPending ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Sparkles className="size-4" />
          )}
          {t('reloadLatest')}
        </Button>
        <Button type="button" variant="outline" size="sm" onClick={onDiscard}>
          {t('discard')}
        </Button>
      </div>
    </div>
  )
}

export function SystemLlmReadiness({ isSuperUser }: { readonly isSuperUser: boolean }) {
  const t = useTranslations('skill.builderDialog.systemLlm')
  return (
    <div className="moldy-status-surface moldy-status-warn flex items-start gap-3 rounded-md p-3">
      <AlertTriangle className="moldy-status-icon mt-0.5 size-4 shrink-0" />
      <div>
        <p className="moldy-status-text text-sm font-semibold">{t('title')}</p>
        <p className="moldy-status-muted-text mt-1 text-xs">
          {isSuperUser ? t('adminDescription') : t('userDescription')}
        </p>
        {isSuperUser ? (
          <Button
            className="mt-3"
            render={<Link href="/settings/system-llm" />}
            size="sm"
            variant="outline"
          >
            {t('settingsAction')}
          </Button>
        ) : null}
      </div>
    </div>
  )
}
