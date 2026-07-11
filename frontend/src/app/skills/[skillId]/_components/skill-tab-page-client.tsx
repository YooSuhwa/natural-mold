'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { ArrowLeftIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { buttonVariants } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { PackageSkillEditor } from '@/components/skill/skill-detail-package-editor'
import { TextSkillEditor } from '@/components/skill/skill-detail-text-editor'
import { SkillEvaluationTab } from '@/components/skill/skill-evaluation-tab'
import { SkillHistoryTab } from '@/components/skill/skill-history-tab'
import { useSkill } from '@/lib/hooks/use-skills'
import { cn } from '@/lib/utils'
import type { Skill } from '@/lib/types/skill'

import type { SkillScopedStudioTab } from '../../_lib/skill-studio-tabs'
import { renderSkillStudioTabShell } from './skill-studio-tab-shell'
import { SkillRevisionSourceViewer } from './skill-revision-source-viewer'
import { SkillSettingsSections } from './skill-settings-sections'

/**
 * 스킬 스코프 탭 페이지 (평가/버전/소스/설정) — 기존 상세 다이얼로그의 탭
 * 컴포넌트를 4슬롯 렌더 프롭 계약 그대로 풀페이지 셸로 렌더한다 (스펙 AD-3).
 */
export function SkillTabPageClient({
  skillId,
  tab,
  revisionId = null,
}: {
  readonly skillId: string
  readonly tab: SkillScopedStudioTab
  /** 소스 탭 전용 — 리비전 read-only 모드 (`?revision=`). */
  readonly revisionId?: string | null
}) {
  const t = useTranslations('skill.studio')
  const { data: skill, isLoading, isError } = useSkill(skillId)

  if (isLoading) {
    return (
      <div className="moldy-app-surface flex min-h-0 flex-1 overflow-hidden p-3">
        <div className="moldy-panel flex-1 space-y-4 p-6">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-24 w-full" />
        </div>
      </div>
    )
  }

  if (isError || !skill) {
    return (
      <div className="moldy-app-surface flex min-h-0 flex-1 items-center justify-center p-3">
        <div className="moldy-panel max-w-md space-y-3 p-6 text-center">
          <h2 className="text-base font-semibold">{t('skillUnavailableTitle')}</h2>
          <p className="text-sm text-muted-foreground">{t('skillUnavailableHint')}</p>
          <Link href="/skills" className={cn(buttonVariants({ variant: 'outline' }))}>
            <ArrowLeftIcon className="size-4" />
            {t('backToList')}
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="moldy-app-surface flex min-h-0 flex-1 overflow-hidden p-3">
      <div className="moldy-panel flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <SkillTabBody skill={skill} tab={tab} revisionId={revisionId} />
      </div>
    </div>
  )
}

function SkillTabBody({
  skill,
  tab,
  revisionId,
}: {
  readonly skill: Skill
  readonly tab: SkillScopedStudioTab
  readonly revisionId: string | null
}) {
  const router = useRouter()

  if (tab === 'evaluation') {
    return renderSkillStudioTabShell({
      body: (
        <SkillEvaluationTab
          skillId={skill.id}
          skillContentHash={skill.content_hash}
          needsCredentialSetup={skill.health?.state === 'needs_credentials'}
          onOpenCredentials={() => router.push(`/skills/${skill.id}/settings`)}
        />
      ),
      footer: null,
    })
  }
  if (tab === 'versions') {
    return <SkillHistoryTab skillId={skill.id}>{renderSkillStudioTabShell}</SkillHistoryTab>
  }
  if (tab === 'settings') {
    return renderSkillStudioTabShell({
      body: <SkillSettingsSections skill={skill} />,
      footer: null,
    })
  }
  // source + ?revision= — 리비전 스냅샷 read-only 뷰어 (M4).
  if (revisionId) {
    return <SkillRevisionSourceViewer skillId={skill.id} revisionId={revisionId} />
  }
  // source — 저장(=리비전 생성)은 유지, 삭제/내보내기/자격증명은 설정 탭 소유 (D1/D2).
  if (skill.kind === 'text') {
    return (
      <TextSkillEditor skillId={skill.id} showCredentials={false} showDangerZone={false}>
        {renderSkillStudioTabShell}
      </TextSkillEditor>
    )
  }
  return (
    <PackageSkillEditor skillId={skill.id} showCredentials={false} showDangerZone={false}>
      {renderSkillStudioTabShell}
    </PackageSkillEditor>
  )
}
