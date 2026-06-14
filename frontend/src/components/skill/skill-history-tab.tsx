'use client'

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'

import { DialogShell } from '@/components/shared/dialog-shell'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useSkillRevisions } from '@/lib/hooks/use-skill-revisions'

export function SkillHistoryTab({
  skillId,
  onClose,
}: {
  readonly skillId: string
  readonly onClose: () => void
}) {
  const t = useTranslations('skill.detailDialog.history')
  const common = useTranslations('skill.detailDialog')
  const { data: revisions, isLoading } = useSkillRevisions(skillId)
  const items = useMemo(() => revisions ?? [], [revisions])

  return (
    <>
      <DialogShell.Body>
        {isLoading ? (
          <Skeleton className="h-32 w-full rounded-lg" />
        ) : items.length === 0 ? (
          <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            {t('empty')}
          </div>
        ) : (
          <div className="space-y-2">
            {items.map((revision) => (
              <article key={revision.id} className="rounded-lg border border-border/70 p-3">
                <div className="flex items-center justify-between gap-3">
                  <h3 className="text-sm font-semibold">
                    {t('revision', { number: revision.revision_number })}
                  </h3>
                  <span className="font-mono moldy-ui-micro text-muted-foreground">
                    {revision.content_hash?.slice(0, 8) ?? '--------'}
                  </span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  {revision.changelog_summary ?? revision.operation}
                </p>
                <p className="mt-2 moldy-ui-micro text-muted-foreground">
                  {t('files', { count: revision.file_count })}
                </p>
              </article>
            ))}
          </div>
        )}
      </DialogShell.Body>
      <DialogShell.Footer>
        <Button variant="outline" onClick={onClose}>
          {common('close')}
        </Button>
      </DialogShell.Footer>
    </>
  )
}
