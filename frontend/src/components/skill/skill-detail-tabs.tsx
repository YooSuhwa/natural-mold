'use client'

import { FileText, History, KeyRound, ListChecks, Settings2 } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { LineTabsList, LineTabsTrigger } from '@/components/ui/line-tabs'
import type { Skill } from '@/lib/types/skill'

export type SkillDetailTab = 'content' | 'credentials' | 'evaluation' | 'history' | 'metadata'

const TABS: readonly SkillDetailTab[] = [
  'content',
  'credentials',
  'evaluation',
  'history',
  'metadata',
]

const ACTIONABLE_EVALUATION_HEALTH_STATES = new Set([
  'needs_rerun',
  'evaluation_running',
  'evaluation_failed',
  'low_confidence',
])

function iconForTab(tab: SkillDetailTab) {
  switch (tab) {
    case 'content':
      return FileText
    case 'credentials':
      return KeyRound
    case 'evaluation':
      return ListChecks
    case 'history':
      return History
    case 'metadata':
      return Settings2
  }
}

export function coerceSkillDetailTab(value: string | null | undefined): SkillDetailTab {
  if (!value) return 'content'
  switch (value) {
    case 'content':
    case 'credentials':
    case 'evaluation':
    case 'history':
    case 'metadata':
      return value
    default:
      return 'content'
  }
}

export function getVisibleSkillDetailTabs(
  skill: Skill,
  initialTab: SkillDetailTab = 'content',
): readonly SkillDetailTab[] {
  const tabs: SkillDetailTab[] = ['content']

  if (
    initialTab === 'credentials' ||
    skill.health?.state === 'needs_credentials' ||
    (skill.credential_requirements?.length ?? 0) > 0
  ) {
    tabs.push('credentials')
  }

  if (
    initialTab === 'evaluation' ||
    hasEvaluationEvidence(skill) ||
    (skill.health ? ACTIONABLE_EVALUATION_HEALTH_STATES.has(skill.health.state) : false)
  ) {
    tabs.push('evaluation')
  }

  if (initialTab === 'history' || !!skill.current_revision_id) {
    tabs.push('history')
  }

  tabs.push('metadata')

  return tabs
}

function hasEvaluationEvidence(skill: Skill): boolean {
  const summary = skill.latest_evaluation_summary
  if (!summary) return false
  return (
    summary.status !== 'missing' ||
    Boolean(summary.latest_run_id) ||
    Boolean(summary.evaluation_set_id)
  )
}

export function SkillDetailTabs({
  visibleTabs = TABS,
}: {
  readonly visibleTabs?: readonly SkillDetailTab[]
}) {
  const t = useTranslations('skill.detailDialog.tabs')

  return (
    <div className="border-b border-border/60 px-6">
      <LineTabsList className="w-full justify-start overflow-x-auto">
        {visibleTabs.map((tab) => {
          const Icon = iconForTab(tab)
          return (
            <LineTabsTrigger key={tab} value={tab}>
              <Icon className="size-3.5" />
              {t(tab)}
            </LineTabsTrigger>
          )
        })}
      </LineTabsList>
    </div>
  )
}
