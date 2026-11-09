'use client'

import { useRef, useState } from 'react'
import { BracesIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Textarea } from '@/components/ui/textarea'
import type { TemplateSegment } from '@/lib/types/workflow'

import {
  refKey,
  refToken,
  referencedValues,
  segmentsToText,
  textToSegments,
} from '../lib/template-text'
import type { UpstreamValue } from '../lib/upstream-values'

export type LabelOf = (node: string, path: string) => string

/**
 * Text with values from earlier steps. Type ``{{`` or press the button to pick a value;
 * the box shows ``{{node.field}}`` and the draft stores reference segments. Committed on
 * blur so one edit is one undo step.
 */
export function TemplateField({
  label,
  value,
  values,
  labelOf,
  rows = 3,
  onCommit,
}: {
  label: string
  value: readonly TemplateSegment[] | undefined
  values: readonly UpstreamValue[]
  labelOf: LabelOf
  rows?: number
  onCommit: (segments: TemplateSegment[]) => void
}) {
  const initial = segmentsToText(value)
  return (
    <TemplateFieldInner
      key={initial}
      label={label}
      initial={initial}
      values={values}
      labelOf={labelOf}
      rows={rows}
      onCommit={onCommit}
    />
  )
}

function TemplateFieldInner({
  label,
  initial,
  values,
  labelOf,
  rows,
  onCommit,
}: {
  label: string
  initial: string
  values: readonly UpstreamValue[]
  labelOf: LabelOf
  rows: number
  onCommit: (segments: TemplateSegment[]) => void
}) {
  const t = useTranslations('workflows.settings')
  const [text, setText] = useState(initial)
  const [picking, setPicking] = useState(false)
  const box = useRef<HTMLTextAreaElement>(null)
  const commit = (next: string) => {
    if (next !== initial) onCommit(textToSegments(next))
  }
  const insert = (item: UpstreamValue) => {
    const area = box.current
    const at = area?.selectionStart ?? text.length
    const before = text.slice(0, at).replace(/\{\{$/, '')
    const next = `${before}${refToken(item.node, item.path, item.previous === true)}${text.slice(at)}`
    setText(next)
    setPicking(false)
    commit(next)
  }
  const known = new Set(values.map((item) => refKey(item)))
  const used = referencedValues(textToSegments(text))
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">{label}</span>
        <Popover open={picking} onOpenChange={setPicking}>
          <PopoverTrigger
            render={
              <Button variant="ghost" size="sm" type="button">
                <BracesIcon className="size-3.5" aria-hidden />
                {t('insertValue')}
              </Button>
            }
          />
          <PopoverContent side="bottom" align="end" className="w-72 p-1">
            <ValueList values={values} labelOf={labelOf} onPick={insert} />
          </PopoverContent>
        </Popover>
      </div>
      <Textarea
        ref={box}
        rows={rows}
        value={text}
        aria-label={label}
        onChange={(event) => {
          setText(event.target.value)
          const at = event.target.selectionStart ?? event.target.value.length
          if (event.target.value.slice(0, at).endsWith('{{')) setPicking(true)
        }}
        onBlur={() => commit(text)}
      />
      {used.length > 0 ? (
        <ul className="flex flex-wrap gap-1" aria-label={t('passedValues')}>
          {used.map((ref) => (
            <li
              key={refKey(ref)}
              className={
                known.has(refKey(ref))
                  ? 'moldy-status-surface moldy-status-info rounded-full px-2 py-0.5 text-xs'
                  : 'moldy-status-surface moldy-status-danger rounded-full px-2 py-0.5 text-xs'
              }
            >
              {ref.previous ? `${t('previousRound')} · ` : ''}
              {labelOf(ref.node, ref.path)}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

export function ValueList({
  values,
  labelOf,
  onPick,
}: {
  values: readonly UpstreamValue[]
  labelOf: LabelOf
  onPick: (value: UpstreamValue) => void
}) {
  const t = useTranslations('workflows.settings')
  if (values.length === 0) {
    return <p className="p-2 text-sm text-muted-foreground">{t('noValues')}</p>
  }
  return (
    <ul className="flex max-h-72 flex-col overflow-auto" aria-label={t('insertValue')}>
      {values.map((item) => (
        <li key={refKey(item)}>
          <button
            type="button"
            className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent focus-visible:bg-accent"
            onClick={() => onPick(item)}
          >
            <span className="truncate">
              {item.previous ? `${t('previousRound')} · ` : ''}
              {labelOf(item.node, item.path)}
            </span>
            <span className="shrink-0 text-xs text-muted-foreground">{item.type}</span>
          </button>
        </li>
      ))}
    </ul>
  )
}
