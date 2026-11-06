'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { FormFieldShell } from '@/components/shared/form-field-shell'
import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { useUpdateGuardrailPolicy } from '@/lib/hooks/use-guardrails'
import type { GuardrailPolicy, GuardrailPolicyMode } from '@/lib/types/guardrails'
import { useGuardrailErrorMessage, useRuleLabel } from '../lib/labels'
import { GuardrailRuleList } from './guardrail-rule-list'

const MODES: readonly GuardrailPolicyMode[] = ['off', 'open', 'default', 'enforced']
const NO_TEMPLATE = 'none'

interface GuardrailPolicyFormProps {
  tenantId: string
  policy: GuardrailPolicy
  readOnly: boolean
}

/** Company mode, default template and locked rules (plan §3.4). */
export function GuardrailPolicyForm({ tenantId, policy, readOnly }: GuardrailPolicyFormProps) {
  const t = useTranslations('guardrails')
  const ruleLabel = useRuleLabel()
  const errorMessage = useGuardrailErrorMessage()
  const update = useUpdateGuardrailPolicy(tenantId)
  const [mode, setMode] = useState(policy.mode)
  const [templateRef, setTemplateRef] = useState(policy.default_template ?? NO_TEMPLATE)
  const [locked, setLocked] = useState(() => new Set(policy.locked_rules))
  const template = policy.templates.find((item) => item.ref === templateRef) ?? null
  // Locks only name rules of the chosen default template.
  const lockedRules = template ? template.rules.filter((rule) => locked.has(rule.id)) : []
  const needsTemplate = mode === 'default' || mode === 'enforced'
  const valid = !needsTemplate || template !== null
  const dirty =
    mode !== policy.mode ||
    (template?.ref ?? null) !== policy.default_template ||
    lockedRules.map((rule) => rule.id).join() !== [...policy.locked_rules].sort().join()

  function toggleLock(ruleId: string, on: boolean) {
    setLocked((prev) => {
      const next = new Set(prev)
      if (on) next.add(ruleId)
      else next.delete(ruleId)
      return next
    })
  }

  function save() {
    update.mutate(
      {
        data: {
          mode,
          default_template: template?.ref ?? null,
          locked_rules: lockedRules.map((rule) => rule.id).sort(),
        },
        version: policy.updated_at,
      },
      {
        onSuccess: () => toast.success(t('policy.saved')),
        onError: (err) => toast.error(errorMessage(err)),
      },
    )
  }

  return (
    <SettingsSectionCard
      title={t('policy.title')}
      description={readOnly ? t('policy.readOnly') : t('policy.description')}
    >
      <div className="space-y-5">
        {policy.saved ? null : (
          <p className="text-sm text-muted-foreground">{t('policy.notSaved')}</p>
        )}
        <fieldset className="space-y-2">
          <legend className="moldy-ui-label text-foreground">{t('policy.mode.label')}</legend>
          <div className="flex flex-wrap gap-2">
            {MODES.map((option) => (
              <Button
                key={option}
                size="sm"
                variant={mode === option ? 'default' : 'outline'}
                aria-pressed={mode === option}
                disabled={readOnly}
                onClick={() => setMode(option)}
              >
                {t(`policy.mode.${option}`)}
              </Button>
            ))}
          </div>
          <p className="text-sm text-muted-foreground">{t(`policy.mode.${mode}Description`)}</p>
        </fieldset>
        {mode === 'off' ? null : (
          <>
            <FormFieldShell
              id="guardrail-default-template"
              label={t('policy.defaultTemplate')}
              description={t('policy.defaultTemplateHint')}
              required={needsTemplate}
            >
              <Select
                value={templateRef}
                disabled={readOnly}
                onValueChange={(value) => setTemplateRef(String(value))}
              >
                <SelectTrigger id="guardrail-default-template" className="w-full">
                  <span>{template ? template.name : t('policy.noTemplate')}</span>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NO_TEMPLATE}>{t('policy.noTemplate')}</SelectItem>
                  {policy.templates.map((item) => (
                    <SelectItem key={item.ref} value={item.ref}>
                      {`${item.name} · ${t(`templateKind.${item.kind}`)} v${item.version}`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </FormFieldShell>
            {template ? <GuardrailRuleList rules={template.rules} /> : null}
            <fieldset className="space-y-2">
              <legend className="moldy-ui-label text-foreground">{t('policy.locked.label')}</legend>
              <p className="text-sm text-muted-foreground">{t('policy.locked.description')}</p>
              {template ? (
                <ul className="space-y-2">
                  {template.rules.map((rule) => (
                    <li key={rule.id}>
                      <label className="flex items-center gap-2 text-sm">
                        <Checkbox
                          checked={locked.has(rule.id)}
                          disabled={readOnly}
                          onCheckedChange={(checked) => toggleLock(rule.id, checked === true)}
                        />
                        {ruleLabel(rule.id)}
                      </label>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">{t('policy.locked.empty')}</p>
              )}
            </fieldset>
          </>
        )}
        {readOnly ? null : (
          <Button disabled={!dirty || !valid || update.isPending} onClick={save}>
            {t('policy.save')}
          </Button>
        )}
      </div>
    </SettingsSectionCard>
  )
}
