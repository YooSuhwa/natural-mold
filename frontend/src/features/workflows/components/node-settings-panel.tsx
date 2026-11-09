'use client'

import Link from 'next/link'
import { ExternalLinkIcon, Trash2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { useAgents } from '@/lib/hooks/use-agents'
import { cn } from '@/lib/utils'
import type {
  EdgeWhen,
  TemplateSegment,
  WorkflowDefinition,
  WorkflowInput,
  WorkflowNode,
} from '@/lib/types/workflow'

import { exitsOf, loopCandidates, nextCandidates } from '../lib/editor-commands'
import { MAX_ROUNDS } from '../lib/loops'
import { contractLine } from '../lib/schema-rows'
import { referencedValues } from '../lib/template-text'
import { upstreamValues } from '../lib/upstream-values'
import { ConditionEditor, InputsEditor, OutputSchemaEditor } from './field-editors'
import { TemplateField, type LabelOf } from './template-field'

const SELECT_CLASS =
  'h-9 w-full rounded-md border bg-background px-2 text-sm focus-visible:ring-2 focus-visible:ring-ring'

type Props = {
  definition: WorkflowDefinition
  node: WorkflowNode
  labelOf: LabelOf
  onChange: (node: WorkflowNode) => void
  onInputsChange: (inputs: WorkflowInput[]) => void
  /** Point one exit at another step (``null`` disconnects it). */
  onNext: (when: EdgeWhen | null, target: string | null) => void
  onDelete: () => void
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold text-muted-foreground">{title}</h3>
      {children}
    </section>
  )
}

/** The right-hand step settings (workflow-plan 3.7, appendix D): one form per node kind. */
export function NodeSettingsPanel({
  definition,
  node,
  labelOf,
  onChange,
  onInputsChange,
  onNext,
  onDelete,
}: Props) {
  const t = useTranslations('workflows')
  const values = upstreamValues(definition, node.id)
  const field = (
    label: string,
    value: TemplateSegment[] | undefined,
    apply: (v: TemplateSegment[]) => WorkflowNode,
    rows?: number,
  ) => (
    <TemplateField
      label={label}
      value={value}
      values={values}
      labelOf={labelOf}
      rows={rows}
      onCommit={(segments) => onChange(apply(segments))}
    />
  )
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <span className="text-xs text-muted-foreground">{t(`kind.${node.type}`)}</span>
        {node.type === 'start' ? (
          <h2 className="text-base font-semibold">{t('kind.start')}</h2>
        ) : (
          <Input
            key={`${node.id}:${node.label ?? ''}`}
            defaultValue={node.label ?? ''}
            placeholder={t(`kind.${node.type}`)}
            aria-label={t('settings.nodeLabel')}
            onBlur={(event) => {
              const label = event.target.value.trim()
              if (label !== (node.label ?? '')) onChange({ ...node, label: label || null })
            }}
          />
        )}
        <span className="text-xs text-muted-foreground">
          {t('editor.nodeId')}: {node.id}
        </span>
      </div>

      {node.type === 'start' ? (
        <Section title={t('settings.inputs')}>
          <p className="text-xs text-muted-foreground">{t('settings.inputsHint')}</p>
          <InputsEditor inputs={definition.inputs} onChange={onInputsChange} />
        </Section>
      ) : null}

      {node.type === 'agent' ? (
        <AgentSettings node={node} onChange={onChange}>
          {field(t('settings.prompt'), node.prompt, (prompt) => ({ ...node, prompt }), 5)}
          <PassedValues segments={node.prompt} labelOf={labelOf} />
          <Section title={t('settings.output')}>
            <p className="text-xs text-muted-foreground">
              {t('settings.outputHint', { fields: contractLine(node.output_schema) || '-' })}
            </p>
            <OutputSchemaEditor
              schema={node.output_schema}
              onChange={(output_schema) => onChange({ ...node, output_schema })}
            />
          </Section>
        </AgentSettings>
      ) : null}

      {node.type === 'decision_bool' ? (
        <>
          <Section title={t('settings.evaluator')}>
            <div
              className="grid grid-cols-3 gap-1"
              role="radiogroup"
              aria-label={t('settings.evaluator')}
            >
              {(['rule', 'llm', 'model'] as const).map((kind) => (
                <Button
                  key={kind}
                  type="button"
                  size="sm"
                  variant={kind === 'rule' ? 'default' : 'outline'}
                  role="radio"
                  aria-checked={kind === 'rule'}
                  disabled={kind !== 'rule'}
                  title={kind === 'rule' ? undefined : t('settings.evaluatorLater')}
                >
                  {t(`settings.evaluatorKind.${kind}`)}
                </Button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">{t('settings.evaluatorLater')}</p>
          </Section>
          <Section title={t('settings.condition')}>
            <ConditionEditor
              condition={node.condition}
              values={values}
              labelOf={labelOf}
              onChange={(condition) => onChange({ ...node, condition })}
            />
          </Section>
          {node.repeat ? (
            <Section title={t('loop.title')}>
              <Input
                key={`${node.id}:${node.repeat.max_rounds}`}
                type="number"
                min={1}
                max={MAX_ROUNDS}
                defaultValue={node.repeat.max_rounds}
                aria-label={t('loop.maxRounds')}
                onBlur={(event) => {
                  const rounds = Math.min(
                    MAX_ROUNDS,
                    Math.max(1, Math.round(Number(event.target.value) || 1)),
                  )
                  if (rounds !== node.repeat?.max_rounds) {
                    onChange({ ...node, repeat: { max_rounds: rounds } })
                  }
                }}
              />
              <p className="text-xs text-muted-foreground">
                {t('loop.maxRoundsHint', { max: MAX_ROUNDS })}
              </p>
            </Section>
          ) : null}
        </>
      ) : null}

      {node.type === 'approval' ? (
        <>
          {field(t('settings.approvalTitle'), node.title, (title) => ({ ...node, title }), 2)}
          {field(t('settings.approvalBody'), node.body, (body) => ({ ...node, body }), 4)}
          <Section title={t('settings.timeout')}>
            <Input
              key={`${node.id}:${node.timeout_minutes ?? ''}`}
              type="number"
              min={1}
              max={10080}
              defaultValue={node.timeout_minutes ?? ''}
              aria-label={t('settings.timeout')}
              onBlur={(event) => {
                const minutes = Number(event.target.value)
                const next = event.target.value === '' ? null : Math.round(minutes)
                if (next !== (node.timeout_minutes ?? null)) {
                  onChange({ ...node, timeout_minutes: next })
                }
              }}
            />
            <p className="text-xs text-muted-foreground">{t('settings.timeoutHint')}</p>
          </Section>
        </>
      ) : null}

      {node.type === 'transform' ? (
        <>
          {field(t('settings.template'), node.template, (template) => ({ ...node, template }), 4)}
          <FieldTemplates
            title={t('settings.fields')}
            fields={node.fields ?? {}}
            render={(name, value) =>
              field(name, value, (segments) => ({
                ...node,
                fields: { ...(node.fields ?? {}), [name]: segments },
              }))
            }
            onChange={(fields) => onChange({ ...node, fields })}
          />
        </>
      ) : null}

      {node.type === 'output' ? (
        <FieldTemplates
          title={t('settings.outputFields')}
          fields={node.fields}
          render={(name, value) =>
            field(name, value, (segments) => ({
              ...node,
              fields: { ...node.fields, [name]: segments },
            }))
          }
          onChange={(fields) => onChange({ ...node, fields })}
        />
      ) : null}

      {node.type === 'fail' ? (
        <Section title={t('settings.reason')}>
          <Textarea
            key={`${node.id}:${node.reason ?? ''}`}
            rows={2}
            maxLength={500}
            defaultValue={node.reason ?? ''}
            aria-label={t('settings.reason')}
            onBlur={(event) => {
              if (event.target.value !== (node.reason ?? '')) {
                onChange({ ...node, reason: event.target.value })
              }
            }}
          />
        </Section>
      ) : null}

      <NextSteps definition={definition} node={node} labelOf={labelOf} onNext={onNext} />

      {node.type !== 'start' ? (
        <Button variant="outline" size="sm" onClick={onDelete}>
          <Trash2Icon className="size-4" aria-hidden />
          {t('editor.deleteNode')}
        </Button>
      ) : null}
    </div>
  )
}

function AgentSettings({
  node,
  onChange,
  children,
}: {
  node: Extract<WorkflowNode, { type: 'agent' }>
  onChange: (node: WorkflowNode) => void
  children: React.ReactNode
}) {
  const t = useTranslations('workflows.settings')
  const agents = useAgents()
  const known = (agents.data ?? []).some((agent) => agent.id === node.agent_id)
  return (
    <>
      <Section title={t('agent')}>
        <select
          className={SELECT_CLASS}
          value={known ? node.agent_id : ''}
          aria-label={t('agent')}
          onChange={(event) => onChange({ ...node, agent_id: event.target.value })}
        >
          {known ? null : <option value="">{t('pickAgent')}</option>}
          {(agents.data ?? []).map((agent) => (
            <option key={agent.id} value={agent.id}>
              {agent.name}
            </option>
          ))}
        </select>
        {known ? (
          <Link
            href={`/agents/${node.agent_id}/settings`}
            className="flex items-center gap-1 text-xs text-primary-strong underline"
          >
            <ExternalLinkIcon className="size-3" aria-hidden />
            {t('openAgent')}
          </Link>
        ) : null}
        <p className="text-xs text-muted-foreground">{t('agentPinned')}</p>
      </Section>
      {children}
    </>
  )
}

function PassedValues({ segments, labelOf }: { segments: TemplateSegment[]; labelOf: LabelOf }) {
  const t = useTranslations('workflows.settings')
  const refs = referencedValues(segments)
  return (
    <Section title={t('passedValues')}>
      {refs.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t('passedNone')}</p>
      ) : (
        <ul className="flex flex-col gap-1">
          {refs.map((ref) => (
            <li
              key={`${ref.node}${ref.path}`}
              className="moldy-muted-panel flex items-center justify-between gap-2 px-2 py-1 text-xs"
            >
              <span className="truncate">{labelOf(ref.node, ref.path)}</span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-xs text-muted-foreground">{t('passedHint')}</p>
    </Section>
  )
}

function FieldTemplates({
  title,
  fields,
  render,
  onChange,
}: {
  title: string
  fields: Record<string, TemplateSegment[]>
  render: (name: string, value: TemplateSegment[]) => React.ReactNode
  onChange: (fields: Record<string, TemplateSegment[]>) => void
}) {
  const t = useTranslations('workflows.settings')
  const names = Object.keys(fields)
  return (
    <Section title={title}>
      {names.map((name) => (
        <div key={name} className="flex flex-col gap-1">
          {render(name, fields[name])}
          <Button
            variant="ghost"
            size="sm"
            className={cn('self-end')}
            onClick={() => {
              const next = { ...fields }
              delete next[name]
              onChange(next)
            }}
          >
            {t('removeField')}
          </Button>
        </div>
      ))}
      <Input
        placeholder={t('newFieldName')}
        aria-label={t('newFieldName')}
        onKeyDown={(event) => {
          const name = event.currentTarget.value.trim()
          if (event.key !== 'Enter' || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(name) || name in fields)
            return
          onChange({ ...fields, [name]: [] })
          event.currentTarget.value = ''
        }}
      />
    </Section>
  )
}

/** Where each exit leads: re-wiring without deleting and adding the step again. */
function NextSteps({
  definition,
  node,
  labelOf,
  onNext,
}: {
  definition: WorkflowDefinition
  node: WorkflowNode
  labelOf: LabelOf
  onNext: (when: EdgeWhen | null, target: string | null) => void
}) {
  const t = useTranslations('workflows')
  const exits = exitsOf(definition, node.id)
  if (exits.length === 0) return null
  const candidates = nextCandidates(definition, node.id)
  return (
    <Section title={t('settings.nextSteps')}>
      {exits.map((when) => {
        const current = definition.edges.find(
          (edge) => edge.from === node.id && (edge.when ?? null) === when,
        )
        const back = loopCandidates(definition, node.id, when)
        const label =
          when === null
            ? t('settings.nextStep')
            : when === 'limit'
              ? t('loop.onLimit')
              : t('settings.nextOn', { branch: t(`branch.${when}`) })
        return (
          <label key={when ?? 'next'} className="flex flex-col gap-1 text-xs">
            {label}
            <select
              className={SELECT_CLASS}
              value={current?.to ?? ''}
              onChange={(event) => onNext(when, event.target.value || null)}
            >
              <option value="">{t('settings.noNext')}</option>
              {candidates.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {labelOf(candidate.id, '')}
                </option>
              ))}
              {back.length > 0 ? (
                <optgroup label={t('loop.goBack')}>
                  {back.map((candidate) => (
                    <option key={candidate.id} value={candidate.id}>
                      {t('loop.backTo', { step: labelOf(candidate.id, '') })}
                    </option>
                  ))}
                </optgroup>
              ) : null}
            </select>
          </label>
        )
      })}
      <p className="text-xs text-muted-foreground">{t('settings.nextHint')}</p>
    </Section>
  )
}
