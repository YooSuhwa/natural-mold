'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useUpdateGuardrailPolicy } from '@/lib/hooks/use-guardrails'
import type { GuardrailPolicy, ToolRiskActions } from '@/lib/types/guardrails'
import {
  DEFAULT_TOOL_RISK,
  TOOL_RISK_ACTIONS,
  TOOL_RISK_LEVELS,
  useGuardrailErrorMessage,
} from '../lib/labels'

interface GuardrailToolRiskFormProps {
  tenantId: string
  policy: GuardrailPolicy
  readOnly: boolean
}

/**
 * Company action per tool risk level (C5): allow, approval or block. Saved with the rest
 * of the policy as it is now, so this card never changes the mode or templates.
 */
export function GuardrailToolRiskForm({ tenantId, policy, readOnly }: GuardrailToolRiskFormProps) {
  const t = useTranslations('guardrails.toolRisk')
  const errorMessage = useGuardrailErrorMessage()
  const update = useUpdateGuardrailPolicy(tenantId)
  const [actions, setActions] = useState<ToolRiskActions>(policy.tool_risk)
  const dirty = TOOL_RISK_LEVELS.some((level) => actions[level] !== policy.tool_risk[level])

  function save() {
    update.mutate(
      {
        data: {
          mode: policy.mode,
          default_template: policy.default_template,
          locked_rules: policy.locked_rules,
          tool_risk: actions,
        },
        version: policy.updated_at,
      },
      {
        onSuccess: () => toast.success(t('saved')),
        onError: (err) => toast.error(errorMessage(err)),
      },
    )
  }

  return (
    <SettingsSectionCard
      title={t('title')}
      description={readOnly ? t('readOnly') : t('description')}
    >
      <div className="space-y-4">
        <ul className="divide-y divide-border">
          {TOOL_RISK_LEVELS.map((level) => (
            <li key={level} className="flex flex-wrap items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-sm text-foreground">{t(`level.${level}`)}</p>
                <p className="moldy-ui-caption text-muted-foreground">{t(`levelHint.${level}`)}</p>
              </div>
              <fieldset className="flex flex-wrap gap-1.5" aria-label={t(`level.${level}`)}>
                {TOOL_RISK_ACTIONS.map((action) => (
                  <Button
                    key={action}
                    size="sm"
                    variant={actions[level] === action ? 'default' : 'outline'}
                    aria-pressed={actions[level] === action}
                    disabled={readOnly}
                    onClick={() => setActions((prev) => ({ ...prev, [level]: action }))}
                  >
                    {t(`action.${action}`)}
                  </Button>
                ))}
              </fieldset>
              {actions[level] === DEFAULT_TOOL_RISK[level] ? (
                <Badge variant="outline">{t('default')}</Badge>
              ) : null}
            </li>
          ))}
        </ul>
        <p className="text-sm text-muted-foreground">{t('triggerNote')}</p>
        {readOnly ? null : (
          <Button disabled={!dirty || update.isPending} onClick={save}>
            {t('save')}
          </Button>
        )}
      </div>
    </SettingsSectionCard>
  )
}
