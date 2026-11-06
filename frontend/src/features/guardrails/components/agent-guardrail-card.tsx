'use client'

import { useState } from 'react'
import { Loader2Icon, LockIcon, SaveIcon, ShieldCheckIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useAgentGuardrails, useUpdateAgentGuardrails } from '@/lib/hooks/use-guardrails'
import type {
  AgentGuardrailMode,
  AgentGuardrails,
  GuardrailAction,
  GuardrailRule,
  GuardrailRuleOverride,
  ToolRiskActions,
} from '@/lib/types/guardrails'
import {
  DEFAULT_TOOL_RISK,
  GUARDRAIL_ACTIONS,
  TOOL_RISK_LEVELS,
  useGuardrailErrorMessage,
  useRuleLabel,
} from '../lib/labels'

const MODES: readonly AgentGuardrailMode[] = ['inherit', 'template', 'off']

/** The agent settings card: follow the company, pick a template or switch off (plan §3.4). */
export function AgentGuardrailCard({ agentId }: { agentId: string }) {
  const t = useTranslations('guardrails.agent')
  const guardrails = useAgentGuardrails(agentId)
  if (!guardrails.data) {
    return (
      <section className="rounded-lg border px-4 py-3">
        <p className="text-sm text-muted-foreground">
          {guardrails.isLoading ? t('loading') : t('loadFailed')}
        </p>
      </section>
    )
  }
  return (
    <AgentGuardrailForm
      key={JSON.stringify(guardrails.data)}
      agentId={agentId}
      saved={guardrails.data}
    />
  )
}

function AgentGuardrailForm({ agentId, saved }: { agentId: string; saved: AgentGuardrails }) {
  const t = useTranslations('guardrails')
  const ruleLabel = useRuleLabel()
  const errorMessage = useGuardrailErrorMessage()
  const update = useUpdateAgentGuardrails(agentId)
  const enforced = saved.policy_mode === 'enforced'
  const companyOff = saved.policy_mode === 'off'
  const [mode, setMode] = useState<AgentGuardrailMode>(enforced ? 'inherit' : saved.mode)
  const [templateRef, setTemplateRef] = useState(
    saved.template_ref ?? saved.templates[0]?.ref ?? '',
  )
  const [overrides, setOverrides] = useState(saved.rule_overrides)
  const locked = new Set(saved.locked_rules)
  const byRef = (ref: string | null) => saved.templates.find((item) => item.ref === ref) ?? null
  const base =
    mode === 'inherit'
      ? byRef(saved.policy_default_template)
      : mode === 'template'
        ? byRef(templateRef)
        : null
  const changes = base ? overridesFor(base.rules, overrides, locked) : {}
  const payload = {
    mode,
    template_ref: mode === 'template' ? templateRef : null,
    rule_overrides: changes,
  }
  const dirty =
    JSON.stringify(payload) !==
    JSON.stringify({
      mode: saved.mode,
      template_ref: saved.template_ref,
      rule_overrides: saved.rule_overrides,
    })

  const emptyNote =
    mode !== 'off'
      ? t('agent.noDefault')
      : saved.locked_rules.length > 0
        ? t('agent.offLocked', { count: saved.locked_rules.length })
        : t('agent.offNote')

  function setRule(ruleId: string, patch: Partial<GuardrailRuleOverride>) {
    setOverrides((prev) => {
      const current = prev[ruleId] ?? { enabled: true, action: null }
      return { ...prev, [ruleId]: { ...current, ...patch } }
    })
  }

  function save() {
    update.mutate(payload, {
      onSuccess: () => toast.success(t('agent.saved')),
      onError: (err) => toast.error(errorMessage(err)),
    })
  }

  return (
    <section className="rounded-lg border px-4 py-3" data-testid="agent-guardrail-card">
      <div className="mb-3 min-w-0">
        <div className="flex items-center gap-2 text-sm font-medium">
          <ShieldCheckIcon className="size-4 text-muted-foreground" aria-hidden />
          {t('agent.title')}
        </div>
        <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
          {t(`agent.policy.${saved.policy_mode}`)}
        </p>
      </div>

      {companyOff ? null : (
        <div className="space-y-3">
          <fieldset className="space-y-1.5">
            <legend className="text-xs font-medium text-muted-foreground">
              {t('agent.mode.label')}
            </legend>
            <div className="flex flex-wrap gap-1.5">
              {MODES.map((option) => (
                <Button
                  key={option}
                  size="sm"
                  variant={mode === option ? 'default' : 'outline'}
                  aria-pressed={mode === option}
                  disabled={enforced && option !== 'inherit'}
                  onClick={() => setMode(option)}
                >
                  {t(`agent.mode.${option}`)}
                </Button>
              ))}
            </div>
          </fieldset>

          {mode === 'template' ? (
            <div className="space-y-1.5">
              <div
                className="text-xs font-medium text-muted-foreground"
                id="agent-guardrail-template"
              >
                {t('agent.template')}
              </div>
              <Select value={templateRef} onValueChange={(value) => setTemplateRef(String(value))}>
                <SelectTrigger className="w-full" aria-labelledby="agent-guardrail-template">
                  <span className="truncate">{byRef(templateRef)?.name ?? ''}</span>
                </SelectTrigger>
                <SelectContent>
                  {saved.templates.map((item) => (
                    <SelectItem key={item.ref} value={item.ref}>
                      {`${item.name} · ${t(`templateKind.${item.kind}`)} v${item.version}`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}

          {base ? (
            <ul className="divide-y divide-border">
              {base.rules.map((rule) => (
                <RuleRow
                  key={rule.id}
                  rule={rule}
                  label={ruleLabel(rule.id)}
                  locked={locked.has(rule.id)}
                  override={overrides[rule.id]}
                  onChange={(patch) => setRule(rule.id, patch)}
                />
              ))}
            </ul>
          ) : (
            <p className="text-xs leading-5 text-muted-foreground">{emptyNote}</p>
          )}

          <p
            className="text-xs leading-5 text-muted-foreground"
            data-testid="agent-guardrail-effective"
          >
            {saved.effective
              ? t('agent.effective', {
                  name: saved.effective.name,
                  version: saved.effective.version,
                  count: saved.effective.rules.length,
                })
              : t('agent.effectiveNone')}
          </p>

          <div className="flex justify-end">
            <Button size="sm" disabled={!dirty || update.isPending} onClick={save}>
              {update.isPending ? (
                <Loader2Icon className="size-4 animate-spin" aria-hidden />
              ) : (
                <SaveIcon className="size-4" aria-hidden />
              )}
              {t('agent.save')}
            </Button>
          </div>
        </div>
      )}
      <ToolRiskSummary actions={saved.tool_risk} />
    </section>
  )
}

/** Only real changes on rules the company left open are sent. */
function overridesFor(
  rules: readonly GuardrailRule[],
  overrides: Record<string, GuardrailRuleOverride>,
  locked: ReadonlySet<string>,
): Record<string, GuardrailRuleOverride> {
  const result: Record<string, GuardrailRuleOverride> = {}
  for (const rule of rules) {
    const change = overrides[rule.id]
    if (!change || locked.has(rule.id)) continue
    const action = change.action && change.action !== rule.action ? change.action : null
    if (change.enabled === false || action !== null) {
      result[rule.id] = { enabled: change.enabled, action }
    }
  }
  return result
}

function RuleRow({
  rule,
  label,
  locked,
  override,
  onChange,
}: {
  rule: GuardrailRule
  label: string
  locked: boolean
  override: GuardrailRuleOverride | undefined
  onChange: (patch: Partial<GuardrailRuleOverride>) => void
}) {
  const t = useTranslations('guardrails')
  const enabled = locked || override?.enabled !== false
  const action: GuardrailAction = (!locked && override?.action) || rule.action
  const labelId = `agent-guardrail-rule-${rule.id}`
  return (
    <li className="flex flex-wrap items-center gap-2 py-2">
      <Switch
        checked={enabled}
        disabled={locked}
        aria-labelledby={labelId}
        onCheckedChange={(checked) => onChange({ enabled: checked === true })}
      />
      <span id={labelId} className="min-w-0 flex-1 text-sm">
        {label}
      </span>
      {locked ? (
        <Badge variant="outline">
          <LockIcon aria-hidden />
          {t('agent.locked')}
        </Badge>
      ) : null}
      <Select
        value={action}
        disabled={locked || !enabled}
        onValueChange={(value) => onChange({ action: String(value) as GuardrailAction })}
      >
        <SelectTrigger className="w-28" aria-label={t('agent.actionFor', { rule: label })}>
          <span className="truncate">{t(`action.${action}`)}</span>
        </SelectTrigger>
        <SelectContent>
          {GUARDRAIL_ACTIONS.map((item) => (
            <SelectItem key={item} value={item}>
              {t(`action.${item}`)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </li>
  )
}

/** The company's tool risk actions, read-only here (C5). */
function ToolRiskSummary({ actions }: { actions: ToolRiskActions }) {
  const t = useTranslations('guardrails')
  return (
    <div className="mt-3 space-y-1.5 border-t border-border pt-3" data-testid="agent-tool-risk">
      <div className="text-xs font-medium text-muted-foreground">{t('agent.toolRisk.title')}</div>
      <ul className="space-y-1">
        {TOOL_RISK_LEVELS.map((level) => (
          <li key={level} className="flex items-center gap-2 text-sm">
            <span className="min-w-0 flex-1">{t(`toolRisk.level.${level}`)}</span>
            <Badge variant={actions[level] === DEFAULT_TOOL_RISK[level] ? 'outline' : 'secondary'}>
              {t(`toolRisk.action.${actions[level]}`)}
            </Badge>
          </li>
        ))}
      </ul>
      <p className="text-xs leading-5 text-muted-foreground">{t('agent.toolRisk.note')}</p>
    </div>
  )
}
