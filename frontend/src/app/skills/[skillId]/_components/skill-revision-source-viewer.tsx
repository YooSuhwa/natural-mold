'use client'

import { useState } from 'react'
import Link from 'next/link'
import { FileIcon, History } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { buttonVariants } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import {
  useSkillRevision,
  useSkillRevisionFileContent,
  useSkillRevisionFiles,
} from '@/lib/hooks/use-skill-revisions'
import { formatDisplayBytes } from '@/lib/utils/display-format'
import { cn } from '@/lib/utils'

import { renderSkillStudioTabShell } from './skill-studio-tab-shell'

const SKILL_MD = 'SKILL.md'

/**
 * 소스 탭의 리비전 read-only 모드 (`?revision=`) — 스냅샷 파일 목록/내용을
 * 보여준다. 저장/삭제 없음, 바이너리는 목록에만 표시(내용 404 fail-closed).
 */
export function SkillRevisionSourceViewer({
  skillId,
  revisionId,
}: {
  readonly skillId: string
  readonly revisionId: string
}) {
  const t = useTranslations('skill.studio.versions')
  const { data: detail } = useSkillRevision(skillId, revisionId)
  const { data: filesResponse, isLoading } = useSkillRevisionFiles(skillId, revisionId)
  const files = filesResponse?.files ?? []
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const defaultPath =
    files.find((file) => file.path === SKILL_MD && !file.is_binary)?.path ??
    files.find((file) => !file.is_binary)?.path ??
    null
  const activePath = selectedPath ?? defaultPath
  const activeEntry = files.find((file) => file.path === activePath) ?? null
  const content = useSkillRevisionFileContent(
    skillId,
    revisionId,
    activeEntry && !activeEntry.is_binary ? activeEntry.path : null,
  )

  const footer = (
    <>
      <span className="moldy-ui-micro mr-auto flex items-center gap-1.5 text-muted-foreground">
        <History className="size-3.5" />
        {detail
          ? t('revisionSourceLabel', { number: detail.revision_number })
          : t('revisionSourceLoading')}
        <Badge variant="secondary" className="moldy-ui-micro">
          {t('readOnly')}
        </Badge>
      </span>
      <Link
        href={`/skills/${skillId}/versions`}
        className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}
      >
        {t('backToVersions')}
      </Link>
      <Link
        href={`/skills/${skillId}/source`}
        className={cn(buttonVariants({ variant: 'default', size: 'sm' }))}
      >
        {t('backToCurrent')}
      </Link>
    </>
  )

  if (isLoading) {
    return renderSkillStudioTabShell({
      body: <Skeleton className="h-48 w-full rounded-lg" />,
      footer,
    })
  }

  if (filesResponse?.snapshot_pruned) {
    return renderSkillStudioTabShell({
      body: (
        <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
          {t('prunedPlaceholder')}
        </div>
      ),
      footer,
    })
  }

  return renderSkillStudioTabShell({
    sidebar: (
      <nav aria-label={t('revisionFilesAria')} className="space-y-1">
        {files.map((file) => (
          <button
            key={file.path}
            type="button"
            disabled={file.is_binary}
            onClick={() => setSelectedPath(file.path)}
            className={cn(
              'flex w-full items-center gap-1.5 rounded-md px-2 py-1.5 text-left text-xs',
              file.path === activePath ? 'bg-muted font-medium' : 'hover:bg-muted/60',
              file.is_binary && 'cursor-not-allowed opacity-50',
            )}
          >
            <FileIcon className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate font-mono">{file.path}</span>
            <span className="moldy-ui-micro shrink-0 text-muted-foreground">
              {file.is_binary ? t('binaryFile') : formatDisplayBytes(file.size)}
            </span>
          </button>
        ))}
      </nav>
    ),
    sidebarClassName: 'w-64',
    body:
      activeEntry === null ? (
        <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
          {t('noTextFiles')}
        </div>
      ) : content.isError ? (
        <div className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
          {t('fileUnavailable')}
        </div>
      ) : content.isLoading ? (
        <Skeleton className="h-48 w-full rounded-lg" />
      ) : (
        <pre className="h-full overflow-auto rounded-md border border-border/60 bg-muted/30 p-3 font-mono text-xs whitespace-pre-wrap break-all">
          {content.data?.content ?? ''}
        </pre>
      ),
    footer,
  })
}
