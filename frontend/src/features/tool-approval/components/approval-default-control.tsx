'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'

import type { ToolApprovalClause, ToolApprovalToolV1 } from '@/lib/types/runtime-policy'

import { RuleEditor, SELECT_CLASS } from './tool-approval-settings'

type Choice = 'none' | ToolApprovalToolV1['mode']

const EMPTY_CLAUSE: ToolApprovalClause = { source: 'args', path: '', op: 'exists' }

/**
 * A tool's (or MCP server's) own approval default (tool-governance-plan 3.3): the value an
 * agent starts from when it attaches the tool. It is a default, not a floor — the agent's
 * own setting is more specific, and the company's policy bounds both.
 */
export function ApprovalDefaultControl({
  id,
  label,
  noneLabel,
  value,
  disabled = false,
  compact = false,
  onChange,
}: {
  /** Unique per control on the screen (the rule editor keys its inputs by it). */
  id: string
  label: string
  /** What "no default" means here ("기본값 없음", or "서버 기본값을 따름" for an MCP tool). */
  noneLabel: string
  value: ToolApprovalToolV1 | null | undefined
  disabled?: boolean
  /** A table cell: no visible label or hint. */
  compact?: boolean
  /** Saves the value; the control shows it until the returned promise settles. */
  onChange: (next: ToolApprovalToolV1 | null) => void | Promise<void>
}) {
  const t = useTranslations('toolApproval')
  // A condition is sent once it names an argument; until then it lives here.
  const [draft, setDraft] = useState<ToolApprovalClause | null>(null)
  // What was just sent, shown until the save (and the refetch behind it) settles.
  const [sent, setSent] = useState<ToolApprovalToolV1 | null | undefined>(undefined)
  const shown = sent === undefined ? value : sent
  const saved = shown?.mode === 'when' ? (shown.rule?.all?.[0] ?? EMPTY_CLAUSE) : null
  const clause = draft ?? saved
  const choice: Choice = draft ? 'when' : (shown?.mode ?? 'none')

  function send(next: ToolApprovalToolV1 | null) {
    setDraft(null)
    setSent(next)
    void Promise.resolve(onChange(next)).finally(() => setSent(undefined))
  }

  function pick(next: Choice) {
    if (next === 'when') setDraft(saved ?? EMPTY_CLAUSE)
    else send(next === 'none' ? null : { mode: next })
  }

  function edit(next: ToolApprovalClause) {
    if (next.path) send({ mode: 'when', rule: { all: [next] } })
    else setDraft(next)
  }

  const select = (
    <select
      className={SELECT_CLASS}
      value={choice}
      aria-label={label}
      disabled={disabled}
      data-approval-default={id}
      onChange={(event) => pick(event.target.value as Choice)}
    >
      <option value="none">{noneLabel}</option>
      {(['always', 'never', 'when'] as const).map((mode) => (
        <option key={mode} value={mode}>
          {t(`toolMode.${mode}`)}
        </option>
      ))}
    </select>
  )
  const rule = clause && !disabled ? <RuleEditor tool={id} clause={clause} onChange={edit} /> : null
  if (compact) {
    return (
      <div className="flex flex-col items-end gap-1">
        {select}
        {rule}
      </div>
    )
  }
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium">{label}</span>
        {select}
      </div>
      {rule}
      <p className="moldy-ui-caption text-muted-foreground">{t('default.hint')}</p>
    </div>
  )
}
