'use client'

import { PlusIcon, Trash2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { useAgents } from '@/lib/hooks/use-agents'
import { useResourceAccess } from '@/lib/hooks/use-resource-sharing'
import {
  MAX_CASES,
  MAX_FOR_EACH,
  MAX_PARALLEL,
  TOOL_ARGS,
  type ParallelBranch,
  type TemplateSegment,
  type WorkflowApprover,
  type WorkflowNode,
  type WorkflowToolName,
} from '@/lib/types/workflow'

import { contractLine } from '../lib/schema-rows'
import type { UpstreamValue } from '../lib/upstream-values'
import { CommitInput, OutputSchemaEditor, ValuePicker } from './field-editors'
import type { LabelOf } from './template-field'

const SELECT_CLASS =
  'h-9 w-full rounded-md border bg-background px-2 text-sm focus-visible:ring-2 focus-visible:ring-ring'
const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/
const TOOLS: readonly WorkflowToolName[] = ['web_search', 'web_scraper', 'current_datetime']

/** Builds a template field for this node (the panel's ``field`` helper). */
export type FieldOf = (
  label: string,
  value: TemplateSegment[] | undefined,
  apply: (segments: TemplateSegment[]) => WorkflowNode,
  rows?: number,
) => React.ReactNode

type Common = {
  values: readonly UpstreamValue[]
  labelOf: LabelOf
  field: FieldOf
  onChange: (node: WorkflowNode) => void
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold text-muted-foreground">{title}</h3>
      {children}
    </section>
  )
}

function AgentSelect({
  value,
  label,
  onChange,
}: {
  value: string
  label: string
  onChange: (agentId: string) => void
}) {
  const t = useTranslations('workflows.settings')
  const agents = useAgents()
  const known = (agents.data ?? []).some((agent) => agent.id === value)
  return (
    <select
      className={SELECT_CLASS}
      value={known ? value : ''}
      aria-label={label}
      onChange={(event) => onChange(event.target.value)}
    >
      {known ? null : <option value="">{t('pickAgent')}</option>}
      {(agents.data ?? []).map((agent) => (
        <option key={agent.id} value={agent.id}>
          {agent.name}
        </option>
      ))}
    </select>
  )
}

function Minutes({
  value,
  label,
  onCommit,
}: {
  value: number | null | undefined
  label: string
  onCommit: (minutes: number | null) => void
}) {
  return (
    <CommitInput
      type="number"
      min={1}
      max={10080}
      value={value ? String(value) : ''}
      aria-label={label}
      onCommit={(text) => onCommit(text === '' ? null : Math.round(Number(text)))}
    />
  )
}

function SchemaSection({
  schema,
  onChange,
}: {
  schema: Record<string, unknown>
  onChange: (schema: Record<string, unknown>) => void
}) {
  const t = useTranslations('workflows.settings')
  return (
    <Section title={t('output')}>
      <p className="text-xs text-muted-foreground">
        {t('outputHint', { fields: contractLine(schema) || '-' })}
      </p>
      <OutputSchemaEditor schema={schema} onChange={onChange} />
    </Section>
  )
}

/** The LLM way of a decision (N3/N4): a question the system model answers yes or no. */
export function DecisionQuestion({
  node,
  field,
}: {
  node: Extract<WorkflowNode, { type: 'decision_bool' }>
  field: FieldOf
}) {
  const t = useTranslations('workflows.settings')
  return (
    <>
      {field(t('question'), node.question ?? [], (question) => ({ ...node, question }), 3)}
      <p className="text-xs text-muted-foreground">{t('llmDecisionHint')}</p>
    </>
  )
}

export function SwitchSettings({
  node,
  values,
  labelOf,
  onChange,
}: Common & { node: Extract<WorkflowNode, { type: 'switch' }> }) {
  const t = useTranslations('workflows.settings')
  const setCase = (index: number, change: Partial<{ name: string; value: string }>) =>
    onChange({
      ...node,
      cases: node.cases.map((item, at) => (at === index ? { ...item, ...change } : item)),
    })
  return (
    <>
      <Section title={t('switchSource')}>
        <ValuePicker
          source={node.source}
          path={node.path}
          values={values}
          labelOf={labelOf}
          label={t('switchSource')}
          onChange={(source, path) => onChange({ ...node, source, path })}
        />
      </Section>
      <Section title={t('cases')}>
        {node.cases.map((item, index) => (
          <div key={index} className="flex items-center gap-2">
            <CommitInput
              className="h-8 w-28"
              value={item.name}
              aria-label={t('caseName', { n: index + 1 })}
              onCommit={(name) => IDENTIFIER.test(name) && setCase(index, { name })}
            />
            <CommitInput
              className="h-8 flex-1"
              value={String(item.value)}
              aria-label={t('caseValue', { n: index + 1 })}
              onCommit={(value) => setCase(index, { value })}
            />
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('removeCase', { n: index + 1 })}
              disabled={node.cases.length === 1}
              onClick={() =>
                onChange({ ...node, cases: node.cases.filter((_, at) => at !== index) })
              }
            >
              <Trash2Icon className="size-4" aria-hidden />
            </Button>
          </div>
        ))}
        <Button
          variant="outline"
          size="sm"
          disabled={node.cases.length >= MAX_CASES}
          onClick={() => {
            const taken = new Set(node.cases.map((item) => item.name))
            let n = node.cases.length + 1
            while (taken.has(`case_${n}`)) n += 1
            onChange({ ...node, cases: [...node.cases, { name: `case_${n}`, value: '' }] })
          }}
        >
          <PlusIcon className="size-4" aria-hidden />
          {t('addCase')}
        </Button>
        <p className="text-xs text-muted-foreground">{t('casesHint')}</p>
      </Section>
    </>
  )
}

export function LlmSettings({
  node,
  field,
  onChange,
}: Common & { node: Extract<WorkflowNode, { type: 'llm' }> }) {
  const t = useTranslations('workflows.settings')
  return (
    <>
      {field(t('llmPrompt'), node.prompt, (prompt) => ({ ...node, prompt }), 5)}
      <p className="text-xs text-muted-foreground">{t('llmHint')}</p>
      <SchemaSection
        schema={node.output_schema}
        onChange={(output_schema) => onChange({ ...node, output_schema })}
      />
    </>
  )
}

export function AskSettings({
  node,
  field,
  onChange,
}: Common & { node: Extract<WorkflowNode, { type: 'ask' }> }) {
  const t = useTranslations('workflows.settings')
  return (
    <>
      {field(t('question'), node.question, (question) => ({ ...node, question }), 3)}
      <Section title={t('timeout')}>
        <Minutes
          value={node.timeout_minutes}
          label={t('timeout')}
          onCommit={(timeout_minutes) => onChange({ ...node, timeout_minutes })}
        />
        <p className="text-xs text-muted-foreground">{t('askTimeoutHint')}</p>
      </Section>
    </>
  )
}

export function NotifySettings({
  node,
  field,
}: Common & { node: Extract<WorkflowNode, { type: 'notify' }> }) {
  const t = useTranslations('workflows.settings')
  return (
    <>
      {field(t('notifyTitle'), node.title, (title) => ({ ...node, title }), 2)}
      {field(t('notifyBody'), node.body, (body) => ({ ...node, body }), 4)}
      <p className="text-xs text-muted-foreground">{t('notifyHint')}</p>
    </>
  )
}

export function ForEachSettings({
  node,
  values,
  labelOf,
  field,
  onChange,
}: Common & { node: Extract<WorkflowNode, { type: 'for_each' }> }) {
  const t = useTranslations('workflows.settings')
  const lists = values.filter((item) => item.type.endsWith('[]') && !item.previous)
  return (
    <>
      <Section title={t('forEachSource')}>
        <ValuePicker
          source={node.source}
          path={node.path}
          values={lists}
          labelOf={labelOf}
          label={t('forEachSource')}
          onChange={(source, path) => onChange({ ...node, source, path })}
        />
        <p className="text-xs text-muted-foreground">{t('forEachHint', { max: MAX_FOR_EACH })}</p>
      </Section>
      <Section title={t('agent')}>
        <AgentSelect
          value={node.agent_id}
          label={t('agent')}
          onChange={(agent_id) => onChange({ ...node, agent_id })}
        />
      </Section>
      {field(t('prompt'), node.prompt, (prompt) => ({ ...node, prompt }), 4)}
      <Section title={t('maxItems')}>
        <CommitInput
          type="number"
          min={1}
          max={MAX_FOR_EACH}
          value={String(node.max_items ?? MAX_FOR_EACH)}
          aria-label={t('maxItems')}
          onCommit={(text) =>
            onChange({
              ...node,
              max_items: Math.min(MAX_FOR_EACH, Math.max(1, Math.round(Number(text) || 1))),
            })
          }
        />
      </Section>
      <SchemaSection
        schema={node.output_schema}
        onChange={(output_schema) => onChange({ ...node, output_schema })}
      />
    </>
  )
}

export function ParallelSettings({
  node,
  field,
  onChange,
}: Common & { node: Extract<WorkflowNode, { type: 'parallel' }> }) {
  const t = useTranslations('workflows.settings')
  const setBranch = (index: number, change: Partial<ParallelBranch>) =>
    onChange({
      ...node,
      branches: node.branches.map((branch, at) =>
        at === index ? { ...branch, ...change } : branch,
      ),
    })
  return (
    <>
      <p className="text-xs text-muted-foreground">{t('parallelHint', { max: MAX_PARALLEL })}</p>
      {node.branches.map((branch, index) => (
        <section
          key={index}
          className="moldy-muted-panel flex flex-col gap-2 p-2"
          aria-label={t('branchTitle', { name: branch.name })}
        >
          <div className="flex items-center gap-2">
            <CommitInput
              className="h-8 flex-1"
              value={branch.name}
              aria-label={t('branchName', { n: index + 1 })}
              onCommit={(name) => IDENTIFIER.test(name) && setBranch(index, { name })}
            />
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('removeBranch', { name: branch.name })}
              disabled={node.branches.length <= 2}
              onClick={() =>
                onChange({ ...node, branches: node.branches.filter((_, at) => at !== index) })
              }
            >
              <Trash2Icon className="size-4" aria-hidden />
            </Button>
          </div>
          <AgentSelect
            value={branch.agent_id}
            label={t('branchAgent', { name: branch.name })}
            onChange={(agent_id) => setBranch(index, { agent_id })}
          />
          {field(
            t('branchPrompt', { name: branch.name }),
            branch.prompt,
            (prompt) => ({
              ...node,
              branches: node.branches.map((item, at) =>
                at === index ? { ...item, prompt } : item,
              ),
            }),
            3,
          )}
          <OutputSchemaEditor
            schema={branch.output_schema}
            onChange={(output_schema) => setBranch(index, { output_schema })}
          />
        </section>
      ))}
      <Button
        variant="outline"
        size="sm"
        disabled={node.branches.length >= MAX_PARALLEL}
        onClick={() => {
          const taken = new Set(node.branches.map((branch) => branch.name))
          let n = node.branches.length + 1
          while (taken.has(`branch_${n}`)) n += 1
          const first = node.branches[0]
          onChange({
            ...node,
            branches: [
              ...node.branches,
              {
                name: `branch_${n}`,
                agent_id: first?.agent_id ?? '',
                prompt: [],
                output_schema: first?.output_schema ?? {},
              },
            ],
          })
        }}
      >
        <PlusIcon className="size-4" aria-hidden />
        {t('addBranch')}
      </Button>
    </>
  )
}

export function ToolSettings({
  node,
  field,
  onChange,
}: Common & { node: Extract<WorkflowNode, { type: 'tool' }> }) {
  const t = useTranslations('workflows.settings')
  return (
    <>
      <Section title={t('tool')}>
        <select
          className={SELECT_CLASS}
          value={node.tool}
          aria-label={t('tool')}
          onChange={(event) => {
            const tool = event.target.value as WorkflowToolName
            const args = Object.fromEntries(
              TOOL_ARGS[tool].map((name) => [name, node.args?.[name] ?? []]),
            )
            onChange({ ...node, tool, args })
          }}
        >
          {TOOLS.map((tool) => (
            <option key={tool} value={tool}>
              {t(`toolName.${tool}`)}
            </option>
          ))}
        </select>
        <p className="text-xs text-muted-foreground">{t('toolHint')}</p>
      </Section>
      {TOOL_ARGS[node.tool].map((name) =>
        field(
          t(`toolArg.${name}`),
          node.args?.[name] ?? [],
          (segments) => ({ ...node, args: { ...(node.args ?? {}), [name]: segments } }),
          2,
        ),
      )}
    </>
  )
}

/**
 * Who decides an approval (workflow-starts-sharing-plan 3.4): the runner, the workflow's
 * owner, or a person it is shared with. A named person who loses access hands the decision
 * back to the runner at run time; publishing checks they can run it.
 */
export function ApproverSettings({
  node,
  workflowId,
  canShare,
  onChange,
}: {
  node: Extract<WorkflowNode, { type: 'approval' }>
  workflowId: string
  canShare: boolean
  onChange: (node: WorkflowNode) => void
}) {
  const t = useTranslations('workflows.settings')
  const access = useResourceAccess('workflow', workflowId, canShare)
  const people = (access.data?.items ?? []).filter(
    (entry) => entry.principal.type === 'user' && entry.source !== 'owner',
  )
  const current = node.approver ?? { kind: 'runner' as const }
  const value = current.kind === 'user' ? `user:${current.user_id}` : current.kind
  const named = current.kind === 'user' ? current.user_id : null
  const choose = (next: string) => {
    let approver: WorkflowApprover | null = null
    if (next === 'owner') approver = { kind: 'owner' }
    if (next.startsWith('user:')) approver = { kind: 'user', user_id: next.slice(5) }
    onChange({ ...node, approver })
  }
  return (
    <Section title={t('approver')}>
      <select
        className={SELECT_CLASS}
        value={value}
        aria-label={t('approver')}
        onChange={(event) => choose(event.target.value)}
      >
        <option value="runner">{t('approverKind.runner')}</option>
        <option value="owner">{t('approverKind.owner')}</option>
        {people.map((entry) => (
          <option key={entry.principal.id} value={`user:${entry.principal.id}`}>
            {entry.principal.display_name ?? entry.principal.id}
          </option>
        ))}
        {named && !people.some((entry) => entry.principal.id === named) ? (
          <option value={`user:${named}`}>{t('approverKind.named')}</option>
        ) : null}
      </select>
      <p className="text-xs text-muted-foreground">{t('approverHint')}</p>
    </Section>
  )
}
