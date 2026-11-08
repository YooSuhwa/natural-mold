'use client'

import { useState } from 'react'
import { PlusIcon, Trash2Icon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import type { Clause, ClauseOp, Condition, WorkflowInput } from '@/lib/types/workflow'

import { rowsToSchema, schemaToRows, type FieldRow, type FieldType } from '../lib/schema-rows'
import type { UpstreamValue } from '../lib/upstream-values'
import type { LabelOf } from './template-field'

const FIELD_TYPES: readonly FieldType[] = [
  'string',
  'number',
  'integer',
  'boolean',
  'enum',
  'string_list',
]
const OPS: readonly ClauseOp[] = ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'contains', 'exists']
const SELECT_CLASS =
  'h-8 rounded-md border bg-background px-2 text-sm focus-visible:ring-2 focus-visible:ring-ring'

type CommitInputProps = Omit<
  React.ComponentProps<typeof Input>,
  'value' | 'onChange' | 'onBlur'
> & {
  value: string
  onCommit: (value: string) => void
}

/** A text input that reports once, on blur (one edit = one undo step); a new value from
 * outside (undo, another field) remounts it. */
function CommitInput(props: CommitInputProps) {
  return <CommitInputInner key={props.value} {...props} />
}

function CommitInputInner({ value, onCommit, ...props }: CommitInputProps) {
  const [text, setText] = useState(value)
  return (
    <Input
      {...props}
      value={text}
      onChange={(event) => setText(event.target.value)}
      onBlur={() => text !== value && onCommit(text)}
    />
  )
}

/** The node's output schema as a table; a schema the table cannot show is read-only JSON. */
export function OutputSchemaEditor({
  schema,
  onChange,
}: {
  schema: Record<string, unknown>
  onChange: (schema: Record<string, unknown>) => void
}) {
  const t = useTranslations('workflows.settings')
  const rows = schemaToRows(schema)
  if (rows === null) {
    return (
      <pre className="moldy-muted-panel overflow-auto p-2 text-xs">
        {JSON.stringify(schema, null, 2)}
      </pre>
    )
  }
  const update = (next: FieldRow[]) => onChange(rowsToSchema(next))
  const patch = (index: number, change: Partial<FieldRow>) =>
    update(rows.map((row, at) => (at === index ? { ...row, ...change } : row)))
  return (
    <div className="flex flex-col gap-2">
      {rows.map((row, index) => (
        <div key={`${row.name}:${index}`} className="moldy-muted-panel flex flex-col gap-2 p-2">
          <div className="flex items-center gap-2">
            <CommitInput
              className="h-8 flex-1"
              value={row.name}
              aria-label={t('fieldName')}
              onCommit={(name) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(name) && patch(index, { name })}
            />
            <select
              className={SELECT_CLASS}
              value={row.type}
              aria-label={t('fieldType')}
              onChange={(event) => patch(index, { type: event.target.value as FieldType })}
            >
              {FIELD_TYPES.map((type) => (
                <option key={type} value={type}>
                  {t(`type.${type}`)}
                </option>
              ))}
            </select>
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('removeField')}
              onClick={() => update(rows.filter((_, at) => at !== index))}
            >
              <Trash2Icon className="size-4" aria-hidden />
            </Button>
          </div>
          <label className="flex items-center gap-2 text-xs">
            <Checkbox
              checked={row.required}
              onCheckedChange={(checked) => patch(index, { required: checked === true })}
            />
            {t('required')}
          </label>
          {row.type === 'enum' ? (
            <CommitInput
              className="h-8"
              value={row.options.join(', ')}
              placeholder={t('enumPlaceholder')}
              aria-label={t('enumOptions')}
              onCommit={(text) =>
                patch(index, {
                  options: [
                    ...new Set(
                      text
                        .split(',')
                        .map((item) => item.trim())
                        .filter(Boolean),
                    ),
                  ],
                })
              }
            />
          ) : null}
          <CommitInput
            className="h-8"
            value={row.description}
            placeholder={t('fieldDescription')}
            aria-label={t('fieldDescription')}
            onCommit={(description) => patch(index, { description })}
          />
        </div>
      ))}
      <Button
        variant="outline"
        size="sm"
        onClick={() =>
          update([
            ...rows,
            {
              name: `field_${rows.length + 1}`,
              type: 'string',
              required: true,
              description: '',
              options: [],
            },
          ])
        }
      >
        <PlusIcon className="size-4" aria-hidden />
        {t('addField')}
      </Button>
    </div>
  )
}

/** A rule condition (판단 방식: 규칙): all or any of value comparisons. */
export function ConditionEditor({
  condition,
  values,
  labelOf,
  onChange,
}: {
  condition: Condition
  values: readonly UpstreamValue[]
  labelOf: LabelOf
  onChange: (condition: Condition) => void
}) {
  const t = useTranslations('workflows.settings')
  const mode = condition.any ? 'any' : 'all'
  const clauses = condition.any ?? condition.all ?? []
  const write = (nextMode: 'all' | 'any', next: Clause[]) =>
    onChange(nextMode === 'all' ? { all: next } : { any: next })
  const patch = (index: number, change: Partial<Clause>) =>
    write(
      mode,
      clauses.map((clause, at) => (at === index ? { ...clause, ...change } : clause)),
    )
  const typeOf = (clause: Clause) =>
    values.find((item) => item.node === clause.source && item.path === clause.path)?.type
  return (
    <div className="flex flex-col gap-2">
      <select
        className={SELECT_CLASS}
        value={mode}
        aria-label={t('conditionMode')}
        onChange={(event) => write(event.target.value as 'all' | 'any', clauses)}
      >
        <option value="all">{t('modeAll')}</option>
        <option value="any">{t('modeAny')}</option>
      </select>
      {clauses.map((clause, index) => (
        <div key={index} className="moldy-muted-panel flex flex-col gap-2 p-2">
          <select
            className={SELECT_CLASS}
            value={`${clause.source}|${clause.path}`}
            aria-label={t('conditionValue')}
            onChange={(event) => {
              const [source, path] = event.target.value.split('|')
              patch(index, { source, path })
            }}
          >
            {values.some(
              (item) => item.node === clause.source && item.path === clause.path,
            ) ? null : (
              <option value={`${clause.source}|${clause.path}`}>{t('pickValue')}</option>
            )}
            {values.map((item) => (
              <option key={`${item.node}${item.path}`} value={`${item.node}|${item.path}`}>
                {labelOf(item.node, item.path)}
              </option>
            ))}
          </select>
          <div className="flex items-center gap-2">
            <select
              className={SELECT_CLASS}
              value={clause.op}
              aria-label={t('conditionOp')}
              onChange={(event) => {
                const op = event.target.value as ClauseOp
                patch(index, { op, value: op === 'exists' ? null : clause.value })
              }}
            >
              {OPS.map((op) => (
                <option key={op} value={op}>
                  {t(`op.${op}`)}
                </option>
              ))}
            </select>
            {clause.op === 'exists' ? null : typeOf(clause) === 'boolean' ? (
              <select
                className={SELECT_CLASS}
                value={String(clause.value ?? true)}
                aria-label={t('conditionOperand')}
                onChange={(event) => patch(index, { value: event.target.value === 'true' })}
              >
                <option value="true">{t('boolTrue')}</option>
                <option value="false">{t('boolFalse')}</option>
              </select>
            ) : (
              <CommitInput
                className="h-8 flex-1"
                value={
                  clause.value === null || clause.value === undefined ? '' : String(clause.value)
                }
                aria-label={t('conditionOperand')}
                onCommit={(text) => {
                  const numeric = ['gt', 'gte', 'lt', 'lte'].includes(clause.op)
                  const asNumber = Number(text)
                  patch(index, {
                    value:
                      numeric ||
                      (typeOf(clause) === 'number' && text !== '' && !Number.isNaN(asNumber))
                        ? asNumber
                        : text,
                  })
                }}
              />
            )}
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('removeClause')}
              disabled={clauses.length === 1}
              onClick={() =>
                write(
                  mode,
                  clauses.filter((_, at) => at !== index),
                )
              }
            >
              <Trash2Icon className="size-4" aria-hidden />
            </Button>
          </div>
        </div>
      ))}
      <Button
        variant="outline"
        size="sm"
        disabled={clauses.length >= 20}
        onClick={() => write(mode, [...clauses, { source: 'input', path: '', op: 'exists' }])}
      >
        <PlusIcon className="size-4" aria-hidden />
        {t('addClause')}
      </Button>
    </div>
  )
}

/** The workflow's start inputs: what the run form asks for. */
export function InputsEditor({
  inputs,
  onChange,
}: {
  inputs: readonly WorkflowInput[]
  onChange: (inputs: WorkflowInput[]) => void
}) {
  const t = useTranslations('workflows.settings')
  const patch = (index: number, change: Partial<WorkflowInput>) =>
    onChange(inputs.map((item, at) => (at === index ? { ...item, ...change } : item)))
  return (
    <div className="flex flex-col gap-2">
      {inputs.map((item, index) => (
        <div key={`${item.name}:${index}`} className="moldy-muted-panel flex flex-col gap-2 p-2">
          <div className="flex items-center gap-2">
            <CommitInput
              className="h-8 flex-1"
              value={item.name}
              aria-label={t('inputName')}
              onCommit={(name) => /^[A-Za-z_][A-Za-z0-9_]*$/.test(name) && patch(index, { name })}
            />
            <select
              className={SELECT_CLASS}
              value={item.type}
              aria-label={t('fieldType')}
              onChange={(event) =>
                patch(index, {
                  type: event.target.value as WorkflowInput['type'],
                  max_length: event.target.value === 'string' ? item.max_length : null,
                })
              }
            >
              {(['string', 'number', 'integer', 'boolean'] as const).map((type) => (
                <option key={type} value={type}>
                  {t(`type.${type}`)}
                </option>
              ))}
            </select>
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('removeField')}
              onClick={() => onChange(inputs.filter((_, at) => at !== index))}
            >
              <Trash2Icon className="size-4" aria-hidden />
            </Button>
          </div>
          <CommitInput
            className="h-8"
            value={item.label ?? ''}
            placeholder={t('inputLabel')}
            aria-label={t('inputLabel')}
            onCommit={(label) => patch(index, { label: label || null })}
          />
          <label className="flex items-center gap-2 text-xs">
            <Checkbox
              checked={item.required}
              onCheckedChange={(checked) => patch(index, { required: checked === true })}
            />
            {t('required')}
          </label>
        </div>
      ))}
      <Button
        variant="outline"
        size="sm"
        disabled={inputs.length >= 20}
        onClick={() =>
          onChange([
            ...inputs,
            { name: `input_${inputs.length + 1}`, type: 'string', required: true },
          ])
        }
      >
        <PlusIcon className="size-4" aria-hidden />
        {t('addInput')}
      </Button>
    </div>
  )
}
