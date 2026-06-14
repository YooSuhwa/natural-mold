'use client'

import type { Skill } from '@/lib/types/skill'

import { SkillEvaluationSummaryBadge } from './skill-evaluation-summary-badge'
import { SkillHealthBadge } from './skill-health-badge'

export function SkillQualityInline({ skill }: { readonly skill: Skill }) {
  if (!skill.health && !skill.latest_evaluation_summary) return null

  return (
    <>
      <SkillHealthBadge health={skill.health} />
      <SkillEvaluationSummaryBadge summary={skill.latest_evaluation_summary} />
    </>
  )
}
