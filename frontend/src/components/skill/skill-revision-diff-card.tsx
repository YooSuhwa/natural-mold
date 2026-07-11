'use client'

import { useMemo } from 'react'
import Link from 'next/link'
import { FileCode2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { buttonVariants } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useSkillRevisionFileContent } from '@/lib/hooks/use-skill-revisions'
import { cn } from '@/lib/utils'
import type { SkillRevisionDetail, SkillRevisionSummary } from '@/lib/types/skill-revision'

import {
  computeRevisionDiffLines,
  hasRevisionDiffChanges,
  type RevisionDiffLine,
} from './skill-revision-diff-lines'

const SKILL_MD = 'SKILL.md'

// diff 계산/렌더 상한 — 2MB 상한의 병적 입력에서 Myers diff + 라인당 DOM이
// 메인 스레드를 잡는 것을 막는다(초과 시 placeholder, 소스 뷰어로 유도).
const MAX_DIFF_LINES = 5000

/**
 * 선택 리비전 vs parent 리비전의 SKILL.md 라인 diff (Phase 2 목업 ver-diff).
 *
 * 최초 리비전(parent 없음)은 빈 원본 대비 전체 추가로, pruned 스냅샷과
 * 콘텐츠 404(바이너리 등)는 placeholder로 처리한다 — diff 계산은 프론트,
 * 백엔드는 원문만 제공한다 (스펙 AD-6).
 */
export function SkillRevisionDiffCard({
  skillId,
  revision,
  detail,
}: {
  readonly skillId: string
  readonly revision: SkillRevisionSummary
  readonly detail?: SkillRevisionDetail
}) {
  const t = useTranslations('skill.studio.versions')
  const pruned = Boolean(detail?.metadata_json?.snapshot_pruned)
  const parentRevisionId = detail?.parent_revision_id ?? null
  const current = useSkillRevisionFileContent(skillId, pruned ? null : revision.id, SKILL_MD)
  const parent = useSkillRevisionFileContent(skillId, pruned ? null : parentRevisionId, SKILL_MD)

  return (
    <section
      className="space-y-2 rounded-lg border border-border/70 p-3"
      data-testid="revision-diff-card"
    >
      <div className="flex flex-wrap items-center gap-2">
        <FileCode2 className="size-4 text-muted-foreground" />
        <h3 className="text-sm font-semibold">{t('diffTitle')}</h3>
        {parentRevisionId === null && detail ? (
          <Badge variant="secondary" className="moldy-ui-micro">
            {t('initialRevision')}
          </Badge>
        ) : null}
        <Link
          href={`/skills/${skillId}/source?revision=${revision.id}`}
          className={cn(buttonVariants({ variant: 'outline', size: 'sm' }), 'ml-auto')}
        >
          {t('viewRevisionSource')}
        </Link>
      </div>
      <DiffBody
        pruned={pruned}
        detailLoaded={Boolean(detail)}
        currentText={current.data?.content}
        currentError={current.isError}
        currentLoading={current.isLoading}
        parentText={parentRevisionId ? parent.data?.content : ''}
        parentError={parentRevisionId ? parent.isError : false}
        parentLoading={parentRevisionId ? parent.isLoading : false}
      />
    </section>
  )
}

function DiffBody({
  pruned,
  detailLoaded,
  currentText,
  currentError,
  currentLoading,
  parentText,
  parentError,
  parentLoading,
}: {
  readonly pruned: boolean
  readonly detailLoaded: boolean
  readonly currentText: string | undefined
  readonly currentError: boolean
  readonly currentLoading: boolean
  readonly parentText: string | undefined
  readonly parentError: boolean
  readonly parentLoading: boolean
}) {
  const t = useTranslations('skill.studio.versions')

  if (pruned) {
    return <DiffPlaceholder message={t('prunedPlaceholder')} />
  }
  if (!detailLoaded || currentLoading || parentLoading) {
    return <Skeleton className="h-24 w-full rounded-md" />
  }
  if (currentError || currentText === undefined) {
    return <DiffPlaceholder message={t('diffUnavailable')} />
  }
  // parent 스냅샷이 pruned/유실이면 비교 기준이 없다 — 명시 placeholder.
  if (parentError || parentText === undefined) {
    return <DiffPlaceholder message={t('parentUnavailable')} />
  }

  return <DiffLines parentText={parentText} currentText={currentText} />
}

function DiffLines({
  parentText,
  currentText,
}: {
  readonly parentText: string
  readonly currentText: string
}) {
  const t = useTranslations('skill.studio.versions')
  // 부모 리렌더(rollback pending 등)마다 diff를 재계산하지 않는다.
  const lines = useMemo(
    () => computeRevisionDiffLines(parentText, currentText),
    [parentText, currentText],
  )
  if (!hasRevisionDiffChanges(lines)) {
    return <DiffPlaceholder message={t('noChanges')} />
  }
  if (lines.length > MAX_DIFF_LINES) {
    return <DiffPlaceholder message={t('diffTooLarge')} />
  }

  return (
    <pre className="max-h-96 overflow-auto rounded-md border border-border/60 bg-muted/30 p-2 font-mono text-xs">
      {lines.map((line, index) => (
        <DiffLineRow key={index} line={line} />
      ))}
    </pre>
  )
}

function DiffLineRow({ line }: { readonly line: RevisionDiffLine }) {
  const marker = line.type === 'added' ? '+' : line.type === 'removed' ? '-' : ' '
  return (
    <div
      className={cn(
        'whitespace-pre-wrap break-all px-1',
        line.type === 'added' && 'moldy-status-success moldy-status-soft',
        line.type === 'removed' && 'moldy-status-danger moldy-status-soft',
      )}
    >
      {marker} {line.text}
    </div>
  )
}

function DiffPlaceholder({ message }: { readonly message: string }) {
  return (
    <div className="rounded-md border border-dashed p-4 text-center text-sm text-muted-foreground">
      {message}
    </div>
  )
}
