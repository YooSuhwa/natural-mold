import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import type { GoalCriterion } from '@/lib/types/conversation-goal'

export interface CriterionDraft {
  id: string
  kind: GoalCriterion['kind']
  path: string
  description: string
  columns: { id: string; name: string; filter: string }[]
  uniqueColumnId: string
  minimumRows: string
  references: string
}

const normalized = (value: string) => value.normalize('NFKC').trim().toLowerCase()

export function criterionDraft(criterion?: GoalCriterion): CriterionDraft {
  const csv = criterion?.kind === 'csv_table' ? criterion : null
  const names = csv?.required_columns ?? ['']
  const filters = new Map(
    Object.entries(csv?.column_equals ?? {}).map(([key, value]) => [normalized(key), value]),
  )
  const columns = names.map((name) => ({
    id: crypto.randomUUID(),
    name,
    filter: filters.get(normalized(name)) ?? '',
  }))
  return {
    id: crypto.randomUUID(),
    kind: criterion?.kind ?? 'nonempty_file',
    path: criterion && criterion.kind !== 'manual' ? criterion.logical_path : '',
    description: criterion?.kind === 'manual' ? criterion.description : '',
    columns,
    uniqueColumnId: (
      columns.find((col) => normalized(col.name) === normalized(csv?.unique_by ?? '')) ?? columns[0]
    ).id,
    minimumRows: String(csv?.minimum_rows ?? 1),
    references: csv?.reference_rows?.length ? JSON.stringify(csv.reference_rows, null, 2) : '',
  }
}

function referenceRows(value: string, names: string[], uniqueBy: string, minimum: number) {
  if (!value.trim()) return []
  let data: unknown
  try {
    data = JSON.parse(value)
  } catch {
    return null
  }
  if (!Array.isArray(data) || data.length > 100 || (data.length && data.length < minimum))
    return null
  let referenceSize = 0
  const rows: Record<string, string>[] = []
  for (const row of data) {
    if (
      typeof row !== 'object' ||
      row === null ||
      Array.isArray(row) ||
      Object.keys(row).length !== names.length
    )
      return null
    const entries = new Map(Object.entries(row).map(([key, cell]) => [normalized(key), cell]))
    const fields: [string, string][] = []
    for (const name of names) {
      const field: unknown = entries.get(normalized(name))
      if (typeof field !== 'string' || !field.trim() || field.trim().length > 2048) return null
      referenceSize += field.trim().length
      if (referenceSize > 65536) return null
      fields.push([name, field.trim()])
    }
    rows.push(Object.fromEntries(fields))
  }
  if (new Set(rows.map((row) => normalized(row[uniqueBy]))).size !== rows.length) return null
  return rows
}

export function buildCriterion(draft: CriterionDraft): GoalCriterion | null {
  if (draft.kind === 'manual') {
    const description = draft.description.trim()
    return description && description.length <= 2000 ? { kind: 'manual', description } : null
  }
  const logicalPath = draft.path.trim()
  if (
    !logicalPath ||
    logicalPath.length > 500 ||
    logicalPath.includes('\\') ||
    logicalPath.split('/').some((part) => !part || part === '.' || part === '..') ||
    Array.from(logicalPath).some((ch) => ch.charCodeAt(0) < 32)
  )
    return null
  if (draft.kind === 'nonempty_file') return { kind: 'nonempty_file', logical_path: logicalPath }
  if (!/\.(csv|tsv)$/i.test(logicalPath)) return null
  const names = draft.columns.map((col) => col.name.trim())
  const uniqueBy = draft.columns.find((col) => col.id === draft.uniqueColumnId)?.name.trim()
  const minimumRows = Number(draft.minimumRows)
  if (
    !uniqueBy ||
    names.some((name) => !name) ||
    new Set(names.map(normalized)).size !== names.length ||
    !Number.isInteger(minimumRows) ||
    minimumRows < 1 ||
    minimumRows > 10000
  )
    return null
  const refs = referenceRows(draft.references, names, uniqueBy, minimumRows)
  if (!refs) return null
  return {
    kind: 'csv_table',
    logical_path: logicalPath,
    required_columns: names,
    unique_by: uniqueBy,
    minimum_rows: minimumRows,
    column_equals: Object.fromEntries(
      draft.columns
        .filter((col) => col.filter.trim())
        .map((col) => [col.name.trim(), col.filter.trim()]),
    ),
    reference_rows: refs,
  }
}

type Props = {
  draft: CriterionDraft
  index: number
  onChange: (draft: CriterionDraft) => void
  onRemove: () => void
  canRemove: boolean
}

export function GoalCriterionForm({ draft, index, onChange, onRemove, canRemove }: Props) {
  const t = useTranslations('chat.goal')
  const update = (patch: Partial<CriterionDraft>) => onChange({ ...draft, ...patch })
  const updateColumn = (id: string, patch: { name?: string; filter?: string }) =>
    update({ columns: draft.columns.map((col) => (col.id === id ? { ...col, ...patch } : col)) })
  const removeColumn = (id: string) => {
    const columns = draft.columns.filter((col) => col.id !== id)
    update({
      columns,
      uniqueColumnId: id === draft.uniqueColumnId ? columns[0].id : draft.uniqueColumnId,
    })
  }
  const selectStyle = 'w-full rounded-md border border-input bg-background px-2 py-1.5 text-sm'
  return (
    <fieldset className="min-w-0 space-y-3 border-t border-border py-3">
      <legend className="px-1 font-medium">{t('conditionNumber', { number: index + 1 })}</legend>
      <div className="flex items-end gap-2">
        <label className="grid flex-1 gap-1">
          {t('type')}
          <select
            className={selectStyle}
            value={draft.kind}
            onChange={(event) => {
              const kind = event.target.value
              if (kind === 'manual' || kind === 'nonempty_file' || kind === 'csv_table')
                update({ kind })
            }}
          >
            {(['nonempty_file', 'csv_table', 'manual'] as const).map((kind) => (
              <option key={kind} value={kind}>
                {t(`kinds.${kind}`)}
              </option>
            ))}
          </select>
        </label>
        {canRemove && (
          <Button type="button" size="sm" variant="ghost" onClick={onRemove}>
            {t('removeCondition')}
          </Button>
        )}
      </div>
      {draft.kind === 'manual' ? (
        <label className="grid gap-1">
          {t('manualText')}
          <Textarea
            value={draft.description}
            required
            maxLength={2000}
            onChange={(e) => update({ description: e.target.value })}
          />
          <span>{t('manualHint')}</span>
        </label>
      ) : (
        <label className="grid gap-1">
          {t('path')}
          <Input
            value={draft.path}
            required
            maxLength={500}
            placeholder={t('pathHint')}
            onChange={(e) => update({ path: e.target.value })}
          />
        </label>
      )}
      {draft.kind === 'csv_table' && (
        <>
          <p className="text-xs text-muted-foreground">{t('csvHint')}</p>
          {draft.columns.map((col, idx) => (
            <div key={col.id} className="flex flex-wrap items-end gap-2">
              <label className="grid min-w-24 flex-1 gap-1">
                {t('column', { number: idx + 1 })}
                <Input
                  required
                  maxLength={120}
                  value={col.name}
                  onChange={(e) => updateColumn(col.id, { name: e.target.value })}
                />
              </label>
              <label className="grid min-w-24 flex-1 gap-1">
                {t('filter', { number: idx + 1 })}
                <Input
                  maxLength={120}
                  value={col.filter}
                  onChange={(e) => updateColumn(col.id, { filter: e.target.value })}
                />
              </label>
              {draft.columns.length > 1 && (
                <Button
                  size="sm"
                  type="button"
                  variant="ghost"
                  onClick={() => removeColumn(col.id)}
                >
                  {t('removeColumn')}
                </Button>
              )}
            </div>
          ))}
          <Button
            size="sm"
            type="button"
            variant="outline"
            disabled={draft.columns.length >= 32}
            onClick={() =>
              update({
                columns: [...draft.columns, { id: crypto.randomUUID(), name: '', filter: '' }],
              })
            }
          >
            {t('addColumn')}
          </Button>
          <div className="grid gap-2 sm:grid-cols-2">
            <label className="grid gap-1">
              {t('uniqueBy')}
              <select
                className={selectStyle}
                value={draft.uniqueColumnId}
                onChange={(e) => update({ uniqueColumnId: e.target.value })}
              >
                {draft.columns.map((col, idx) => (
                  <option value={col.id} key={col.id}>
                    {col.name || t('column', { number: idx + 1 })}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1">
              {t('minimumRows')}
              <Input
                required
                type="number"
                min={1}
                max={10000}
                value={draft.minimumRows}
                onChange={(e) => update({ minimumRows: e.target.value })}
              />
            </label>
          </div>
          <details>
            <summary className="cursor-pointer text-sm">{t('referenceTitle')}</summary>
            <label className="mt-2 grid gap-1">
              {t('referenceHint')}
              <Textarea
                rows={4}
                value={draft.references}
                onChange={(e) => update({ references: e.target.value })}
              />
            </label>
          </details>
        </>
      )}
    </fieldset>
  )
}
