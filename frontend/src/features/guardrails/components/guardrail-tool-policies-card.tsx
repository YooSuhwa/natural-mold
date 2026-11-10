'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import { SettingsSectionCard } from '@/components/shared/settings-section-card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  RuleEditor,
  SELECT_CLASS,
} from '@/features/tool-approval/components/tool-approval-settings'
import {
  useSaveToolPolicy,
  useToolPolicies,
  useToolPolicyImpact,
  useToolPolicyTargets,
} from '@/lib/hooks/use-guardrails'
import type {
  ToolPolicy,
  ToolPolicyImpact,
  ToolPolicyMode,
  ToolPolicyTarget,
  ToolPolicyUpdate,
} from '@/lib/types/guardrails'
import type { ToolApprovalClause } from '@/lib/types/runtime-policy'
import { useGuardrailErrorMessage } from '../lib/labels'

const MODES: readonly ToolPolicyMode[] = ['always', 'when', 'never']
const EMPTY: ToolPolicyUpdate = {
  enabled: null,
  default_mode: null,
  allowed_modes: null,
  rule: null,
}
const EMPTY_CLAUSE: ToolApprovalClause = { source: 'args', path: '', op: 'exists' }

interface Editing {
  key: string
  name: string
  riskLevel: string | null
  saved: ToolPolicy | null
}

/**
 * Company policy per tool or MCP server (tool-governance-plan 3.4): switch it off, limit
 * the approval modes agents may pick and set the mode it starts from. Members read the
 * list; company admins edit it, and see which agent settings stop applying before saving.
 */
export function GuardrailToolPoliciesCard({
  tenantId,
  isAdmin,
}: {
  tenantId: string
  isAdmin: boolean
}) {
  const t = useTranslations('guardrails.toolPolicies')
  const policies = useToolPolicies(tenantId)
  const targets = useToolPolicyTargets(tenantId, isAdmin)
  const [editing, setEditing] = useState<Editing | null>(null)
  const saved = policies.data?.policies ?? []
  const byKey = new Map((targets.data ?? []).map((target) => [target.key, target]))
  const free = (targets.data ?? []).filter(
    (target) => !saved.some((policy) => policy.resource_key === target.key),
  )

  function add(key: string) {
    const target = byKey.get(key)
    if (!target) return
    setEditing({
      key,
      name: targetLabel(target),
      riskLevel: target.risk_level ?? null,
      saved: null,
    })
  }

  return (
    <SettingsSectionCard
      title={t('title')}
      description={isAdmin ? t('description') : t('readOnly')}
    >
      <div className="space-y-4">
        {saved.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('empty')}</p>
        ) : (
          <ul className="divide-y divide-border">
            {saved.map((policy) => (
              <li key={policy.resource_key} className="flex flex-wrap items-center gap-2 py-3">
                <p className="min-w-0 flex-1 truncate text-sm text-foreground">
                  {byKey.has(policy.resource_key)
                    ? targetLabel(byKey.get(policy.resource_key) as ToolPolicyTarget)
                    : policy.resource_name}
                </p>
                <PolicySummary policy={policy} />
                {isAdmin ? (
                  <Button
                    size="sm"
                    variant="outline"
                    aria-label={t('editOf', { tool: policy.resource_name })}
                    onClick={() =>
                      setEditing({
                        key: policy.resource_key,
                        name: policy.resource_name,
                        riskLevel: byKey.get(policy.resource_key)?.risk_level ?? null,
                        saved: policy,
                      })
                    }
                  >
                    {t('edit')}
                  </Button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {isAdmin && !editing ? (
          <label className="flex flex-wrap items-center gap-2 text-sm">
            <span>{t('add')}</span>
            <select
              className={SELECT_CLASS}
              value=""
              disabled={free.length === 0}
              onChange={(event) => add(event.target.value)}
            >
              <option value="">{t('pick')}</option>
              {(['mcp_server', 'mcp_tool', 'tool', 'builtin'] as const).map((kind) => {
                const items = free.filter((target) => target.kind === kind)
                return items.length === 0 ? null : (
                  <optgroup key={kind} label={t(`kind.${kind}`)}>
                    {items.map((target) => (
                      <option key={target.key} value={target.key}>
                        {targetLabel(target)}
                      </option>
                    ))}
                  </optgroup>
                )
              })}
            </select>
          </label>
        ) : null}
        {isAdmin && editing ? (
          <ToolPolicyEditor
            key={editing.key}
            tenantId={tenantId}
            editing={editing}
            onClose={() => setEditing(null)}
          />
        ) : null}
      </div>
    </SettingsSectionCard>
  )
}

function targetLabel(target: ToolPolicyTarget): string {
  return target.source ? `${target.source} · ${target.name}` : target.name
}

function PolicySummary({ policy }: { policy: ToolPolicy }) {
  const t = useTranslations('guardrails.toolPolicies')
  const mode = useTranslations('toolApproval.toolMode')
  if (policy.enabled === false) return <Badge variant="destructive">{t('off')}</Badge>
  return (
    <>
      {policy.enabled ? <Badge variant="outline">{t('on')}</Badge> : null}
      {policy.allowed_modes ? (
        <Badge variant="outline">
          {t('allowedSummary', { modes: policy.allowed_modes.map((m) => mode(m)).join(', ') })}
        </Badge>
      ) : null}
      {policy.default_mode ? (
        <Badge variant="secondary">
          {t('defaultSummary', { mode: mode(policy.default_mode) })}
        </Badge>
      ) : null}
    </>
  )
}

function sameDraft(a: ToolPolicyUpdate, b: ToolPolicyUpdate): boolean {
  return (
    a.enabled === b.enabled &&
    a.default_mode === b.default_mode &&
    JSON.stringify(a.allowed_modes) === JSON.stringify(b.allowed_modes) &&
    JSON.stringify(a.rule) === JSON.stringify(b.rule)
  )
}

function ToolPolicyEditor({
  tenantId,
  editing,
  onClose,
}: {
  tenantId: string
  editing: Editing
  onClose: () => void
}) {
  const t = useTranslations('guardrails.toolPolicies')
  const mode = useTranslations('toolApproval.toolMode')
  const level = useTranslations('guardrails.toolRisk.level')
  const errorMessage = useGuardrailErrorMessage()
  const save = useSaveToolPolicy(tenantId)
  const impact = useToolPolicyImpact(tenantId)
  const start: ToolPolicyUpdate = editing.saved
    ? {
        enabled: editing.saved.enabled,
        default_mode: editing.saved.default_mode,
        allowed_modes: editing.saved.allowed_modes,
        rule: editing.saved.rule,
      }
    : EMPTY
  const [draft, setDraft] = useState<ToolPolicyUpdate>(start)
  // What saving would undo, for the draft it was asked about.
  const [preview, setPreview] = useState<ToolPolicyImpact | null>(null)

  const allowed = draft.allowed_modes ?? MODES
  const clause = draft.default_mode === 'when' ? (draft.rule?.all?.[0] ?? EMPTY_CLAUSE) : null
  const empty = sameDraft(draft, EMPTY)
  const problem =
    draft.allowed_modes && draft.allowed_modes.length > 1 && !draft.default_mode
      ? t('needsDefault')
      : clause && !clause.path
        ? t('needsRule')
        : null
  const busy = save.isPending || impact.isPending
  const riskLabel = (risk: string | null | undefined) =>
    risk && level.has(risk) ? level(risk) : (risk ?? '')

  function change(next: Partial<ToolPolicyUpdate>) {
    setPreview(null)
    setDraft((prev) => ({ ...prev, ...next }))
  }

  function toggle(item: ToolPolicyMode, checked: boolean) {
    const next = MODES.filter((m) => (m === item ? checked : allowed.includes(m)))
    if (next.length === 0) return
    const keeps = draft.default_mode && next.includes(draft.default_mode)
    change({
      allowed_modes: next.length === MODES.length ? null : next,
      // One allowed mode is the default; a default outside the allowed modes goes.
      default_mode: next.length === 1 ? next[0] : keeps ? draft.default_mode : null,
      rule:
        (next.length === 1 ? next[0] : keeps ? draft.default_mode : null) === 'when'
          ? draft.rule
          : null,
    })
  }

  function write(data: ToolPolicyUpdate) {
    save.mutate(
      { key: editing.key, data },
      {
        onSuccess: (result) => {
          toast.success(result.policy ? t('saved') : t('removed'))
          onClose()
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    )
  }

  function submit() {
    if (preview) return write(draft)
    impact.mutate(
      { key: editing.key, data: draft },
      {
        onSuccess: (result) => {
          if (result.agents.length === 0 && result.warnings.length === 0) write(draft)
          else setPreview(result)
        },
        onError: (err) => toast.error(errorMessage(err)),
      },
    )
  }

  return (
    <div className="moldy-muted-panel space-y-3 p-3" data-tool-policy-editor={editing.key}>
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-medium text-foreground">{editing.name}</p>
        {editing.riskLevel ? <Badge variant="outline">{riskLabel(editing.riskLevel)}</Badge> : null}
      </div>
      <label className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span>{t('enabled.label')}</span>
        <select
          className={SELECT_CLASS}
          value={draft.enabled === null ? 'follow' : draft.enabled ? 'on' : 'off'}
          onChange={(event) =>
            change({
              enabled: event.target.value === 'follow' ? null : event.target.value === 'on',
            })
          }
        >
          <option value="follow">{t('enabled.follow')}</option>
          <option value="on">{t('enabled.on')}</option>
          <option value="off">{t('enabled.off')}</option>
        </select>
      </label>
      {draft.enabled === false ? (
        <p className="moldy-ui-caption text-muted-foreground">{t('offHint')}</p>
      ) : (
        <>
          <fieldset className="space-y-1.5">
            <legend className="text-sm">{t('allowed')}</legend>
            <div className="flex flex-wrap gap-4">
              {MODES.map((item) => (
                <label key={item} className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={allowed.includes(item)}
                    onCheckedChange={(checked) => toggle(item, checked === true)}
                  />
                  {mode(item)}
                </label>
              ))}
            </div>
            <p className="moldy-ui-caption text-muted-foreground">{t('allowedHint')}</p>
          </fieldset>
          <label className="flex flex-wrap items-center justify-between gap-2 text-sm">
            <span>{t('default')}</span>
            <select
              className={SELECT_CLASS}
              value={draft.default_mode ?? 'none'}
              disabled={allowed.length === 1}
              onChange={(event) => {
                const next = event.target.value === 'none' ? null : event.target.value
                change({
                  default_mode: next as ToolPolicyMode | null,
                  rule: next === 'when' ? draft.rule : null,
                })
              }}
            >
              <option value="none">{t('defaultNone')}</option>
              {allowed.map((item) => (
                <option key={item} value={item}>
                  {mode(item)}
                </option>
              ))}
            </select>
          </label>
          {clause ? (
            <RuleEditor
              tool={`company:${editing.key}`}
              clause={clause}
              onChange={(next) => change({ rule: { all: [next] } })}
            />
          ) : null}
        </>
      )}
      {problem ? <p className="text-sm text-muted-foreground">{problem}</p> : null}
      {preview ? (
        <div className="space-y-2" role="status" data-tool-policy-impact>
          {preview.warnings.map((warning) => (
            <p key={warning.code} className="moldy-status-warn rounded-md p-2 text-sm">
              {t(`warning.${warning.code}`, { level: riskLabel(warning.risk_level) })}
            </p>
          ))}
          {preview.agents.length > 0 ? (
            <>
              <p className="text-sm text-foreground">
                {t('impactTitle', { count: preview.total })}
              </p>
              <ul className="space-y-1 text-sm text-muted-foreground">
                {preview.agents.map((agent) => (
                  <li key={`${agent.id}:${agent.tool_key}`}>
                    {t(`impact.${agent.reason}`, {
                      agent: agent.name,
                      tool: agent.tool_name,
                      mode: mode(agent.mode),
                    })}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={
            busy || problem !== null || (empty && !editing.saved) || sameDraft(draft, start)
          }
          onClick={submit}
        >
          {preview ? t('confirm') : t('save')}
        </Button>
        {editing.saved ? (
          <Button size="sm" variant="outline" disabled={busy} onClick={() => write(EMPTY)}>
            {t('remove')}
          </Button>
        ) : null}
        <Button size="sm" variant="ghost" disabled={busy} onClick={onClose}>
          {t('cancel')}
        </Button>
      </div>
    </div>
  )
}
