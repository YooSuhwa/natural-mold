'use client'

import { useMemo } from 'react'
import Link from 'next/link'
import { useAtomValue } from 'jotai'
import { CheckCircle2Icon, FileTextIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { BuilderResultPanel } from '@/components/skill/skill-builder-preview-insights'
import { Badge } from '@/components/ui/badge'
import { buttonVariants } from '@/components/ui/button'
import { chatSkillDraftBriefAtom, chatSkillValidationAtom } from '@/lib/stores/chat-skill-builder'
import type { SkillBuilderSession, SkillDraftPackage } from '@/lib/types'
import { cn } from '@/lib/utils'

/**
 * 스킬 빌더 챗 우측 검증 레일 (스펙 5.2).
 *
 * `moldy.skill_draft`/`moldy.skill_validation` 이벤트(라이브+리로드 replay)로
 * 드래프트 파일 목록·검증 상태·호환성을 표시한다. 패널은 v1 다이얼로그의
 * 순수 컴포넌트(`BuilderResultPanel` 등)를 그대로 재사용한다.
 */
export function SkillBuilderRail({
  conversationId,
  session,
}: {
  readonly conversationId: string
  readonly session: SkillBuilderSession
}) {
  const t = useTranslations('skill.builderChat')
  const briefByConversation = useAtomValue(chatSkillDraftBriefAtom)
  const validationByConversation = useAtomValue(chatSkillValidationAtom)
  const brief = briefByConversation[conversationId]
  const liveValidation = validationByConversation[conversationId]

  // 라이브 이벤트가 세션 스냅샷보다 최신 — 있으면 우선한다. 패널 재사용을 위해
  // 세션 shape에 라이브 값을 덮어씌운 파생 세션을 만든다 (페이로드 스키마 동일).
  const railSession = useMemo<SkillBuilderSession>(() => {
    if (!liveValidation) return session
    const validation = liveValidation.validation_result as SkillBuilderSession['validation_result']
    const compatibility = liveValidation.validation_result
      .compatibility_result as SkillBuilderSession['compatibility_result']
    return {
      ...session,
      validation_result: validation ?? session.validation_result,
      compatibility_result: compatibility ?? session.compatibility_result,
    }
  }, [liveValidation, session])

  // BuilderResultPanel은 draft를 요구하지만 셀렉터는 session 값을 우선한다 —
  // 빈 draft로 충분 (파일 목록은 brief가 담당).
  const emptyDraft = useMemo<SkillDraftPackage>(
    () => ({
      name: brief?.slug ?? 'draft',
      slug: brief?.slug ?? 'draft',
      description: '',
      files: [],
      credential_requirements: [],
      execution_profile: {},
      validation_issues: [],
    }),
    [brief?.slug],
  )

  return (
    <aside
      className="moldy-panel hidden w-96 min-w-0 shrink-0 flex-col gap-3 overflow-y-auto p-4 md:flex"
      data-testid="skill-builder-rail"
    >
      <div className="flex items-center gap-2">
        <h2 className="text-sm font-semibold">{t('railTitle')}</h2>
        {brief?.slug ? <Badge variant="outline">{brief.slug}</Badge> : null}
        {brief && brief.changed_count > 0 ? (
          <Badge variant="secondary" className="ml-auto">
            {t('changedCount', { count: brief.changed_count })}
          </Badge>
        ) : null}
      </div>

      {session.finalized_skill_id ? (
        <div
          className="moldy-muted-panel flex items-center gap-2 p-3"
          data-testid="builder-completed-banner"
        >
          <CheckCircle2Icon className="moldy-status-icon size-4" />
          <span className="flex-1 text-sm">{t('completedTitle')}</span>
          <Link
            href={`/skills?detailId=${session.finalized_skill_id}`}
            className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}
          >
            {t('openSkill')}
          </Link>
        </div>
      ) : null}

      <section className="space-y-2">
        <h3 className="text-xs font-medium text-muted-foreground">{t('filesTitle')}</h3>
        {brief && brief.files.length > 0 ? (
          <ul className="space-y-1" data-testid="builder-draft-files">
            {brief.files.map((file) => (
              <li key={file.path} className="flex items-center gap-2 text-xs text-muted-foreground">
                <FileTextIcon className="size-3 shrink-0" />
                <span className="min-w-0 flex-1 truncate font-mono">{file.path}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">{t('filesEmpty')}</p>
        )}
      </section>

      <BuilderResultPanel session={railSession} draft={emptyDraft} />
    </aside>
  )
}
