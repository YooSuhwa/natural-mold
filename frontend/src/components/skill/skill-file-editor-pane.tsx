'use client'

import { Download, Trash2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { DeleteConfirmInline } from '@/components/shared/delete-confirm-inline'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Textarea } from '@/components/ui/textarea'
import { skillsApi } from '@/lib/api/skills'

import { isImageFile, isPdf, isTextFile } from './skill-detail-file-utils'

type FileEditorPaneProps = {
  readonly skillId: string
  readonly selectedPath: string | null
  readonly currentContent: string
  readonly currentDirty: boolean
  readonly isSkillMd: boolean
  readonly loadingFile: boolean
  readonly confirmingFileDelete: boolean
  readonly deletePending: boolean
  readonly onEdit: (value: string) => void
  readonly onAskDelete: () => void
  readonly onCancelDelete: () => void
  readonly onConfirmDelete: () => void
}

export function FileEditorPane({
  skillId,
  selectedPath,
  currentContent,
  currentDirty,
  isSkillMd,
  loadingFile,
  confirmingFileDelete,
  deletePending,
  onEdit,
  onAskDelete,
  onCancelDelete,
  onConfirmDelete,
}: FileEditorPaneProps) {
  const t = useTranslations('skill.detailDialog')
  if (!selectedPath) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        {t('pickFile')}
      </div>
    )
  }

  return (
    <>
      <div className="flex items-center gap-2">
        <code className="rounded bg-muted/50 px-2 py-0.5 font-mono moldy-ui-caption">
          {selectedPath}
        </code>
        {currentDirty ? (
          <Badge variant="secondary" className="bg-status-warn/15 moldy-ui-micro text-status-warn">
            {t('unsaved')}
          </Badge>
        ) : null}
        {!isSkillMd ? (
          <Button
            size="sm"
            variant="ghost"
            className="ml-auto h-7 px-2 text-xs text-destructive hover:bg-destructive/10 hover:text-destructive"
            onClick={onAskDelete}
          >
            <Trash2 className="size-3.5" />
            {t('deleteFile')}
          </Button>
        ) : (
          <Badge variant="secondary" className="ml-auto moldy-ui-micro text-muted-foreground">
            {t('protected')}
          </Badge>
        )}
      </div>

      {confirmingFileDelete ? (
        <DeleteConfirmInline
          entity={t('fileEntity')}
          onCancel={onCancelDelete}
          onConfirm={onConfirmDelete}
          pending={deletePending}
        />
      ) : null}

      <FileContentArea
        skillId={skillId}
        path={selectedPath}
        content={currentContent}
        loading={loadingFile}
        onEdit={onEdit}
      />
    </>
  )
}

function FileContentArea({
  skillId,
  path,
  content,
  loading,
  onEdit,
}: {
  readonly skillId: string
  readonly path: string
  readonly content: string
  readonly loading: boolean
  readonly onEdit: (value: string) => void
}) {
  const t = useTranslations('skill.detailDialog')
  if (isImageFile(path)) {
    return (
      <div className="flex flex-1 flex-col items-center gap-3 overflow-auto rounded-md border border-border/60 bg-muted/20 p-4">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={skillsApi.fileUrl(skillId, path)}
          alt={path}
          className="moldy-image-preview max-h-[420px] max-w-full"
        />
        <a
          href={skillsApi.fileUrl(skillId, path)}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-primary-strong hover:underline"
        >
          <Download className="size-3" /> {t('openOriginal')}
        </a>
      </div>
    )
  }

  if (isPdf(path)) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-md border border-border/60 bg-muted/20 p-6">
        <p className="text-sm font-medium">{path}</p>
        <a
          href={skillsApi.fileUrl(skillId, path)}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-background px-3 py-1.5 text-xs hover:bg-muted"
        >
          <Download className="size-3.5" /> {t('openPdf')}
        </a>
      </div>
    )
  }

  if (!isTextFile(path)) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-md border border-border/60 bg-muted/20 p-6 text-center">
        <p className="text-sm font-medium">{t('binaryFile')}</p>
        <p className="text-xs text-muted-foreground">{t('binaryUnavailable')}</p>
        <a
          href={skillsApi.fileUrl(skillId, path)}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 rounded-md border border-border/60 bg-background px-3 py-1.5 text-xs hover:bg-muted"
        >
          <Download className="size-3.5" /> {t('download')}
        </a>
      </div>
    )
  }

  if (loading) {
    return <Skeleton className="h-full min-h-[280px] flex-1 rounded-md" />
  }

  return (
    <Textarea
      value={content}
      onChange={(event) => onEdit(event.target.value)}
      className="h-full min-h-[280px] flex-1 resize-none font-mono text-xs"
    />
  )
}
