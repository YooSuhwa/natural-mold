'use client'

import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import type { GuardrailRule } from '@/lib/types/guardrails'
import { useRuleLabel } from '../lib/labels'

/** One line per rule: what it covers, what it does, and where it checks. */
export function GuardrailRuleList({ rules }: { rules: readonly GuardrailRule[] }) {
  const t = useTranslations('guardrails')
  const ruleLabel = useRuleLabel()
  return (
    <ul className="divide-y divide-border">
      {rules.map((rule) => (
        <li key={rule.id} className="flex flex-wrap items-center gap-2 py-2">
          <span className="min-w-0 flex-1 text-sm">{ruleLabel(rule.id)}</span>
          <Badge variant="secondary">{t(`action.${rule.action}`)}</Badge>
          <span className="moldy-ui-caption text-muted-foreground">
            {rule.stages.map((stage) => t(`stage.${stage}`)).join(' · ')}
          </span>
        </li>
      ))}
    </ul>
  )
}
