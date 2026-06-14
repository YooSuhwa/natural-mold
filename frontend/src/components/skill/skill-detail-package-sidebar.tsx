'use client'

import { FilePlus2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import type { SkillFileEntry } from '@/lib/types/skill'

import { SkillPackageTree } from './skill-package-tree'

type SkillDetailPackageSidebarProps = {
  readonly files: SkillFileEntry[]
  readonly selectedPath: string | null
  readonly dirtyCount: number
  readonly adding: boolean
  readonly newPath: string
  readonly addPending: boolean
  readonly onSelect: (path: string) => void
  readonly onToggleAdding: () => void
  readonly onNewPathChange: (path: string) => void
  readonly onAddFile: () => void
  readonly onCancelAdd: () => void
}

export function SkillDetailPackageSidebar({
  files,
  selectedPath,
  dirtyCount,
  adding,
  newPath,
  addPending,
  onSelect,
  onToggleAdding,
  onNewPathChange,
  onAddFile,
  onCancelAdd,
}: SkillDetailPackageSidebarProps) {
  const t = useTranslations('skill.detailDialog')

  return (
    <>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t('files')}
        </h3>
        <Button
          size="sm"
          variant="ghost"
          className="h-7 px-2 text-xs"
          onClick={onToggleAdding}
          aria-label={t('addFile')}
        >
          <FilePlus2 className="size-3.5" />
          {t('add')}
        </Button>
      </div>
      {adding ? (
        <div className="mb-3 space-y-1.5 rounded-md border border-border/60 bg-background p-2">
          <Input
            value={newPath}
            onChange={(event) => onNewPathChange(event.target.value)}
            placeholder={t('newPathPlaceholder')}
            className="h-7 text-xs"
            onKeyDown={(event) => {
              if (event.key === 'Enter') onAddFile()
              if (event.key === 'Escape') onCancelAdd()
            }}
            autoFocus
          />
          <div className="flex gap-1">
            <Button
              size="sm"
              className="h-6 flex-1 moldy-ui-caption"
              onClick={onAddFile}
              disabled={addPending || !newPath.trim()}
            >
              {t('create')}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-6 flex-1 moldy-ui-caption"
              onClick={onCancelAdd}
            >
              {t('cancel')}
            </Button>
          </div>
        </div>
      ) : null}
      <SkillPackageTree files={files} selectedPath={selectedPath} onSelect={onSelect} />
      {dirtyCount > 0 ? (
        <p className="mt-3 moldy-ui-micro text-muted-foreground">
          {t('unsavedCount', { count: dirtyCount })}
        </p>
      ) : null}
    </>
  )
}
