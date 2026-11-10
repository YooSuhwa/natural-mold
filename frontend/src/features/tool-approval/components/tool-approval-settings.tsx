'use client'

import { LockIcon, XIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { useToolApproval } from '@/lib/hooks/use-tool-approval'
import { cn } from '@/lib/utils'
import type {
  RuntimePolicyV1,
  ToolApprovalClause,
  ToolApprovalPath,
  ToolApprovalPolicyV1,
  ToolApprovalToolV1,
} from '@/lib/types/runtime-policy'
import type {
  ToolApprovalKind,
  ToolApprovalPart,
  ToolApprovalTool,
} from '@/lib/types/tool-approval'

const PATHS: readonly ToolApprovalPath[] = ['chat', 'trigger', 'workflow', 'api']
const OPS: readonly ToolApprovalClause['op'][] = [
  'eq',
  'ne',
  'contains',
  'starts_with',
  'ends_with',
  'exists',
]
export const SELECT_CLASS =
  'h-8 rounded-md border bg-background px-2 text-sm focus-visible:ring-2 focus-visible:ring-ring'
const KIND_TONE: Record<ToolApprovalKind, string> = {
  ask: 'moldy-status-warn',
  when: 'moldy-status-info',
  auto: 'moldy-status-success',
  hidden: 'moldy-status-danger',
}
// The recommended runtime policy, as a stored one (tool approval needs a stored policy).
const CUSTOM_POLICY: RuntimePolicyV1 = {
  version: 1,
  filesystem: { mode: 'artifact_write' },
  todo: { enabled: true },
  summarization: { mode: 'auto' },
}

type ToolChoice = 'default' | ToolApprovalToolV1['mode']

/**
 * 도구 승인 (approval-modes-plan 3.5): the agent's mode per run path and per-tool settings,
 * saved with the agent's runtime policy, and the "실제 적용" table the server computes with
 * the same decision a run uses (it shows the saved settings). Company-locked tools show why.
 */
export function ToolApprovalSettings({
  agentId,
  value,
  onValueChange,
}: {
  agentId: string
  value: RuntimePolicyV1 | null
  onValueChange: (value: RuntimePolicyV1 | null) => void
}) {
  const t = useTranslations('toolApproval')
  const view = useToolApproval(agentId)
  const settings: ToolApprovalPolicyV1 = value?.tool_approval ?? {}
  const write = (next: ToolApprovalPolicyV1) =>
    onValueChange({ ...(value ?? CUSTOM_POLICY), tool_approval: next })
  const setPath = (path: ToolApprovalPath, mode: 'ask_all' | 'auto') =>
    write({ ...settings, paths: { ...(settings.paths ?? {}), [path]: mode } })
  const tools = view.data?.tools ?? []
  // A setting is saved under the tool's key. One saved by name (before keys) is read while
  // that name means a single tool, and moves to the key on the next change.
  const shared = new Set(
    tools
      .filter((tool) => tools.some((other) => other.name === tool.name && other !== tool))
      .map((tool) => tool.name),
  )
  const entryOf = (tool: ToolApprovalTool) =>
    settings.tools?.[tool.key] ?? (shared.has(tool.name) ? undefined : settings.tools?.[tool.name])
  const setTool = (tool: ToolApprovalTool, next: ToolApprovalToolV1 | null) => {
    const entries = { ...(settings.tools ?? {}) }
    delete entries[tool.key]
    if (!shared.has(tool.name)) delete entries[tool.name]
    if (next) entries[tool.key] = next
    write({ ...settings, tools: entries })
  }
  // A skill or a sub-agent is only ever named by its key (a name means a tool).
  const parts = view.data?.parts ?? []
  const setPart = (part: ToolApprovalPart, next: ToolApprovalToolV1 | null) => {
    const entries = { ...(settings.tools ?? {}) }
    delete entries[part.key]
    if (next) entries[part.key] = next
    write({ ...settings, tools: entries })
  }
  const known = new Set([
    ...tools.flatMap((tool) => [tool.key, ...(shared.has(tool.name) ? [] : [tool.name])]),
    ...parts.map((part) => part.key),
  ])
  const unresolved = view.data
    ? Object.keys(settings.tools ?? {}).filter((name) => !known.has(name))
    : []
  const dropEntry = (name: string) => {
    const entries = { ...(settings.tools ?? {}) }
    delete entries[name]
    write({ ...settings, tools: entries })
  }
  return (
    <section className="moldy-card flex flex-col gap-4 p-4" aria-labelledby="tool-approval-title">
      <div className="flex flex-col gap-1">
        <h3 id="tool-approval-title" className="font-semibold">
          {t('title')}
        </h3>
        <p className="moldy-ui-caption text-muted-foreground">{t('description')}</p>
        {value === null ? (
          <p className="moldy-ui-caption text-muted-foreground">{t('becomesCustom')}</p>
        ) : null}
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className="text-sm font-medium">{t('modes')}</legend>
        <div className="grid grid-cols-2 gap-2">
          {PATHS.map((path) => (
            <label key={path} className="flex flex-col gap-1 text-xs">
              {t(`path.${path}`)}
              <select
                className={SELECT_CLASS}
                value={settings.paths?.[path] ?? 'ask_all'}
                onChange={(event) => setPath(path, event.target.value as 'ask_all' | 'auto')}
              >
                <option value="ask_all">{t('mode.ask_all')}</option>
                <option value="auto">{t('mode.auto')}</option>
              </select>
            </label>
          ))}
        </div>
        <p className="moldy-ui-caption text-muted-foreground">{t('modesHint')}</p>
      </fieldset>

      <div className="flex flex-col gap-2">
        <h4 className="text-sm font-medium">{t('tools')}</h4>
        {tools.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('noTools')}</p>
        ) : (
          <ul className="flex flex-col divide-y" aria-label={t('tools')}>
            {tools.map((tool) => (
              <ApprovalRow
                key={tool.key}
                row={tool}
                current={entryOf(tool)}
                onSet={(next) => setTool(tool, next)}
              />
            ))}
          </ul>
        )}
        {parts.length > 0 ? (
          <>
            <h4 className="text-sm font-medium">{t('parts')}</h4>
            <p className="moldy-ui-caption text-muted-foreground">{t('partsHint')}</p>
            <ul className="flex flex-col divide-y" aria-label={t('parts')}>
              {parts.map((part) => (
                <ApprovalRow
                  key={part.key}
                  row={part}
                  current={settings.tools?.[part.key]}
                  onSet={(next) => setPart(part, next)}
                />
              ))}
            </ul>
          </>
        ) : null}
        <p className="moldy-ui-caption text-muted-foreground">{t('effectiveHint')}</p>
        {unresolved.length > 0 ? (
          <div className="moldy-status-surface moldy-status-warn flex flex-col gap-2 rounded-md p-2">
            <p className="text-xs">{t('unresolvedHint')}</p>
            <ul className="flex flex-wrap gap-1" aria-label={t('unresolved')}>
              {unresolved.map((name) => (
                <li
                  key={name}
                  className="flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs"
                >
                  <span className="font-mono">{name}</span>
                  <span>· {t(`toolMode.${settings.tools?.[name]?.mode ?? 'default'}`)}</span>
                  <button
                    type="button"
                    className="rounded-full hover:bg-accent"
                    aria-label={t('unresolvedRemove', { tool: name })}
                    onClick={() => dropEntry(name)}
                  >
                    <XIcon className="size-3" aria-hidden />
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </section>
  )
}

/** One tool, skill or sub-agent: its setting and how each run path decides it. */
function ApprovalRow({
  row: tool,
  current,
  onSet,
}: {
  row: ToolApprovalTool | ToolApprovalPart
  current: ToolApprovalToolV1 | undefined
  onSet: (next: ToolApprovalToolV1 | null) => void
}) {
  const t = useTranslations('toolApproval')
  const kind = 'kind' in tool ? tool.kind : null
  const source = 'source' in tool ? tool.source : null
  const choice: ToolChoice = current?.mode ?? 'default'
  const label = source ? `${tool.name} (${source})` : tool.name
  return (
    <li className="flex flex-col gap-2 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className={kind ? 'text-sm' : 'font-mono text-sm'}>{tool.name}</span>
        {source ? <span className="text-xs text-muted-foreground">{source}</span> : null}
        {kind ? <Badge variant="secondary">{t(`partKind.${kind}`)}</Badge> : null}
        {tool.level ? <Badge variant="outline">{t(`level.${tool.level}`)}</Badge> : null}
        {tool.locked ? (
          <span
            className="flex items-center gap-1 text-xs text-muted-foreground"
            title={tool.cells[0]?.reason}
          >
            <LockIcon className="size-3" aria-hidden />
            {tool.cells[0]?.reason}
          </span>
        ) : null}
        <span className="flex-1" />
        <select
          className={SELECT_CLASS}
          value={choice}
          aria-label={t('toolSetting', { tool: label })}
          disabled={tool.locked}
          onChange={(event) => {
            const next = event.target.value as ToolChoice
            if (next === 'default') onSet(null)
            else if (next === 'when') {
              onSet({
                mode: 'when',
                rule: { all: [{ source: 'args', path: '', op: 'exists' }] },
              })
            } else onSet({ mode: next })
          }}
        >
          {(['default', 'always', 'never', 'when'] as const).map((item) => (
            <option
              key={item}
              value={item}
              // A mode the company does not allow would be ignored by the run.
              disabled={
                item !== 'default' &&
                item !== choice &&
                tool.allowed_modes != null &&
                !tool.allowed_modes.includes(item)
              }
            >
              {t(`toolMode.${item}`)}
            </option>
          ))}
        </select>
      </div>
      {tool.allowed_modes && !tool.locked ? (
        <p className="moldy-ui-caption text-muted-foreground">
          {t('companyAllows', {
            modes: tool.allowed_modes.map((item) => t(`toolMode.${item}`)).join(', '),
          })}
        </p>
      ) : null}
      {current?.mode === 'when' ? (
        <RuleEditor
          tool={tool.key}
          clause={current.rule?.all?.[0] ?? { source: 'args', path: '', op: 'exists' }}
          onChange={(clause) => onSet({ mode: 'when', rule: { all: [clause] } })}
        />
      ) : null}
      <ul className="flex flex-wrap gap-1" aria-label={t('effectiveFor', { tool: label })}>
        {tool.cells.map((cell) => (
          <li
            key={cell.path}
            className={cn(
              'moldy-status-surface rounded-full px-2 py-0.5 text-xs',
              KIND_TONE[cell.kind],
            )}
            title={cell.reason}
          >
            {t(`path.${cell.path}`)} · {t(`kind.${cell.kind}`)}
          </li>
        ))}
      </ul>
    </li>
  )
}

/** "Ask when the argument … " — one comparison over the call's args. */
export function RuleEditor({
  tool,
  clause,
  onChange,
}: {
  tool: string
  clause: ToolApprovalClause
  onChange: (clause: ToolApprovalClause) => void
}) {
  const t = useTranslations('toolApproval')
  return (
    <div className="moldy-muted-panel flex flex-wrap items-center gap-2 p-2 text-xs">
      <span>{t('ruleAskWhen')}</span>
      <Input
        key={`${tool}:${clause.path}`}
        className="h-8 w-32"
        defaultValue={clause.path.replace(/^\//, '')}
        placeholder={t('ruleArg')}
        aria-label={t('ruleArg')}
        onBlur={(event) => {
          const name = event.target.value.trim().replace(/^\//, '')
          onChange({ ...clause, path: name ? `/${name}` : '' })
        }}
      />
      <select
        className={SELECT_CLASS}
        value={clause.op}
        aria-label={t('ruleOp')}
        onChange={(event) => {
          const op = event.target.value as ToolApprovalClause['op']
          onChange({ ...clause, op, value: op === 'exists' ? undefined : (clause.value ?? '') })
        }}
      >
        {OPS.map((op) => (
          <option key={op} value={op}>
            {t(`op.${op}`)}
          </option>
        ))}
      </select>
      {clause.op === 'exists' ? null : (
        <Input
          key={`${tool}:${clause.op}:${String(clause.value ?? '')}`}
          className="h-8 w-40"
          defaultValue={String(clause.value ?? '')}
          aria-label={t('ruleValue')}
          onBlur={(event) => onChange({ ...clause, value: event.target.value })}
        />
      )}
    </div>
  )
}
