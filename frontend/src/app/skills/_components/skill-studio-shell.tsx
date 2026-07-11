'use client'

import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { ChevronsUpDown, Sparkles } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { LineTabsList, LineTabsTrigger } from '@/components/ui/line-tabs'
import { Tabs } from '@/components/ui/tabs'
import { SkillSummaryStrip } from '@/components/skill/skill-summary-strip'
import { useBuilderSessionLauncher, useSkillBuilderSession } from '@/lib/hooks/use-skill-builder'
import { useSkill, useSkills } from '@/lib/hooks/use-skills'
import type { Skill } from '@/lib/types/skill'

import {
  deriveSkillStudioContext,
  isSkillScopedStudioTab,
  SKILL_STUDIO_TABS,
  skillStudioTabHref,
  type SkillStudioTab,
} from '../_lib/skill-studio-tabs'

/**
 * 스킬 스튜디오 셸 — 6탭 내비 + 현재 스킬 컨텍스트 바 (Phase 2 스펙 AD-2).
 *
 * layout은 하위 세그먼트 params에 접근할 수 없으므로(Next.js 계약) 클라이언트
 * 훅(pathname)으로 활성 탭/컨텍스트를 파생한다. 빌더 라우트의 컨텍스트 스킬은
 * 세션의 source(개선 원본) → finalized(생성 산출물) 순으로 역참조한다.
 */
export function SkillStudioShell() {
  const t = useTranslations('skill.studio')
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const context = deriveSkillStudioContext(pathname)
  const { data: builderSession } = useSkillBuilderSession(context.sessionId)
  const builderSkillId = context.sessionId
    ? (builderSession?.source_skill_id ?? builderSession?.finalized_skill_id ?? null)
    : null
  // 빌더 인덱스(/skills/builder?skillId=)의 스코프 스킬 — pathname에는 없어
  // 쿼리에서 보충한다. 놓치면 스킬 스코프 탭 4개가 disabled로 오표기된다(리뷰 R).
  const builderIndexSkillId =
    context.activeTab === 'builder' && context.sessionId === null
      ? searchParams.get('skillId')
      : null
  const contextSkillId = context.skillId ?? builderSkillId ?? builderIndexSkillId
  const { data: contextSkill } = useSkill(contextSkillId)
  const launcher = useBuilderSessionLauncher()

  function handleTabChange(value: string) {
    const tab = value as SkillStudioTab
    if (tab === context.activeTab) return
    const href = skillStudioTabHref(tab, contextSkillId)
    if (href) router.push(href)
  }

  function handleSwitchSkill(skill: Skill) {
    const tab = isSkillScopedStudioTab(context.activeTab) ? context.activeTab : 'source'
    router.push(`/skills/${skill.id}/${tab}`)
  }

  function handleImprove() {
    if (!contextSkillId) return
    void launcher.startImprove(contextSkillId)
  }

  const showContextBar = context.activeTab !== 'list'

  return (
    <div className="shrink-0 border-b border-border/60 bg-background">
      <Tabs value={context.activeTab} onValueChange={handleTabChange} className="gap-0">
        <div className="px-4">
          <LineTabsList aria-label={t('tabsAria')} className="w-full justify-start overflow-x-auto">
            {SKILL_STUDIO_TABS.map((tab) => {
              const disabled =
                isSkillScopedStudioTab(tab) && skillStudioTabHref(tab, contextSkillId) === null
              return (
                <LineTabsTrigger
                  key={tab}
                  value={tab}
                  disabled={disabled}
                  data-testid={`studio-tab-${tab}`}
                >
                  {t(`tabs.${tab}`)}
                </LineTabsTrigger>
              )
            })}
          </LineTabsList>
        </div>
      </Tabs>
      {showContextBar ? (
        <SkillStudioContextBar
          skill={contextSkill ?? null}
          isBuilderDraft={context.activeTab === 'builder' && !contextSkillId}
          improvePending={launcher.pending}
          showImprove={context.activeTab !== 'builder' && Boolean(contextSkillId)}
          onSwitchSkill={handleSwitchSkill}
          onImprove={handleImprove}
        />
      ) : null}
    </div>
  )
}

function SkillStudioContextBar({
  skill,
  isBuilderDraft,
  improvePending,
  showImprove,
  onSwitchSkill,
  onImprove,
}: {
  readonly skill: Skill | null
  readonly isBuilderDraft: boolean
  readonly improvePending: boolean
  readonly showImprove: boolean
  readonly onSwitchSkill: (skill: Skill) => void
  readonly onImprove: () => void
}) {
  const t = useTranslations('skill.studio.contextBar')

  if (!skill && !isBuilderDraft) return null

  const passRate = skill?.latest_evaluation_summary?.pass_rate
  const passRateLabel =
    typeof passRate === 'number' ? t('passRateValue', { percent: Math.round(passRate * 100) }) : '—'

  return (
    <div
      data-testid="studio-context-bar"
      className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-border/40 px-4 py-2"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="moldy-ui-micro shrink-0 text-muted-foreground">{t('currentSkill')}</span>
        {skill ? (
          <SkillSwitcher skill={skill} onSwitchSkill={onSwitchSkill} />
        ) : (
          <span className="text-sm font-semibold">{t('newDraft')}</span>
        )}
        {skill ? (
          <span className="moldy-ui-micro hidden truncate font-mono text-muted-foreground sm:inline">
            {skill.slug}
          </span>
        ) : null}
      </div>
      {skill ? (
        <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
          <SkillSummaryStrip skill={skill} />
          <span className="moldy-ui-micro text-muted-foreground">
            {t('passRate')} <span className="font-semibold text-foreground">{passRateLabel}</span>
          </span>
          <span className="moldy-ui-micro text-muted-foreground">
            {t('connectedAgents')}{' '}
            <span className="font-semibold text-foreground">{skill.used_by_count}</span>
          </span>
        </div>
      ) : null}
      {showImprove && skill ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="ml-auto"
          disabled={improvePending}
          onClick={onImprove}
        >
          <Sparkles className="size-3.5" />
          {t('improve')}
        </Button>
      ) : null}
    </div>
  )
}

function SkillSwitcher({
  skill,
  onSwitchSkill,
}: {
  readonly skill: Skill
  readonly onSwitchSkill: (skill: Skill) => void
}) {
  const t = useTranslations('skill.studio.contextBar')

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        data-testid="studio-skill-switcher"
        aria-label={t('switchSkill')}
        className="flex min-w-0 items-center gap-1 text-sm font-semibold hover:text-primary-strong"
      >
        <span className="truncate">{skill.name}</span>
        <Badge variant="secondary" className="moldy-ui-micro shrink-0">
          {skill.kind}
        </Badge>
        <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="max-h-80 overflow-y-auto">
        {/* useSkills는 팝업이 열려 content가 마운트될 때만 구독 — 셸이 모든
            스킬 라우트에서 전체 목록(+enrichment)을 상시 fetch하지 않게 한다. */}
        <SkillSwitcherItems currentSkillId={skill.id} onSwitchSkill={onSwitchSkill} />
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function SkillSwitcherItems({
  currentSkillId,
  onSwitchSkill,
}: {
  readonly currentSkillId: string
  readonly onSwitchSkill: (skill: Skill) => void
}) {
  const t = useTranslations('skill.studio.contextBar')
  const { data: skills } = useSkills()
  const candidates = (skills ?? []).filter((candidate) => candidate.id !== currentSkillId)

  if (candidates.length === 0) {
    return <DropdownMenuItem disabled>{t('noOtherSkills')}</DropdownMenuItem>
  }
  return (
    <>
      {candidates.map((candidate) => (
        <DropdownMenuItem key={candidate.id} onClick={() => onSwitchSkill(candidate)}>
          <span className="truncate">{candidate.name}</span>
          <span className="moldy-ui-micro ml-auto font-mono text-muted-foreground">
            {candidate.slug}
          </span>
        </DropdownMenuItem>
      ))}
    </>
  )
}
