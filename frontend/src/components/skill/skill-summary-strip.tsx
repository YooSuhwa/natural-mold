'use client'

import { Badge } from '@/components/ui/badge'
import type { Skill } from '@/lib/types/skill'

import { SkillEvaluationSummaryBadge } from './skill-evaluation-summary-badge'
import { SkillHealthBadge } from './skill-health-badge'

export function SkillSummaryStrip({ skill }: { readonly skill: Skill }) {
  return (
    <div className="flex flex-wrap items-center justify-end gap-1.5">
      <SkillHealthBadge health={skill.health} />
      <SkillEvaluationSummaryBadge summary={skill.latest_evaluation_summary} />
      {skill.version ? (
        <Badge variant="secondary" className="moldy-ui-micro">
          v{skill.version}
        </Badge>
      ) : null}
    </div>
  )
}
