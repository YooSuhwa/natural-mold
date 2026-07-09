'use client'

import { useTranslations } from 'next-intl'

import { Badge } from '@/components/ui/badge'
import type { SkillCompatibilityResult } from '@/lib/types/skill-builder'

type PortableCompatibilityPanelProps = {
  readonly result?: SkillCompatibilityResult | Readonly<Record<string, unknown>> | null
  readonly dense?: boolean
}

export type TargetStatus = 'pass' | 'warning' | 'error' | 'unknown'

type IssueView = {
  readonly message: string
}

export type TargetView = {
  readonly key: string
  readonly status: TargetStatus
  readonly issues: readonly IssueView[]
}

function isRecord(value: unknown): value is Readonly<Record<string, unknown>> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringValue(value: unknown): string | null {
  return typeof value === 'string' && value.trim().length > 0 ? value : null
}

function compactValue(value: unknown): string {
  if (value === null || value === undefined) {
    return ''
  }
  if (typeof value === 'string') {
    return value
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }
  return 'object'
}

function normalizeStatus(value: unknown): TargetStatus {
  const status = stringValue(value)
  switch (status) {
    case 'pass':
    case 'ok':
    case 'success':
      return 'pass'
    case 'warning':
    case 'warn':
      return 'warning'
    case 'error':
    case 'failed':
    case 'failure':
      return 'error'
    default:
      return 'unknown'
  }
}

function parseIssue(value: unknown): IssueView | null {
  if (!isRecord(value)) {
    const message = compactValue(value)
    return message ? { message } : null
  }
  const message = stringValue(value.message) ?? stringValue(value.summary) ?? compactValue(value)
  if (!message) {
    return null
  }
  const path = stringValue(value.path)
  return { message: path ? `${path}: ${message}` : message }
}

function parseIssues(value: unknown): readonly IssueView[] {
  if (!Array.isArray(value)) {
    return []
  }
  return value.flatMap((item) => {
    const issue = parseIssue(item)
    return issue ? [issue] : []
  })
}

function parseTarget(key: string, value: unknown): TargetView {
  if (!isRecord(value)) {
    return { key, status: normalizeStatus(value), issues: [] }
  }
  return {
    key,
    status: normalizeStatus(value.status ?? value.state ?? value.result),
    issues: parseIssues(value.issues),
  }
}

export function parseTargets(
  result: PortableCompatibilityPanelProps['result'],
): readonly TargetView[] {
  if (!isRecord(result)) {
    return []
  }
  const targets = result.targets
  if (!isRecord(targets)) {
    return []
  }
  return Object.entries(targets).map(([key, value]) => parseTarget(key, value))
}

export function targetLabel(
  key: string,
  t: ReturnType<typeof useTranslations<'skill.compatibility'>>,
) {
  switch (key) {
    case 'openai_codex':
      return t('target.openaiCodex')
    case 'claude_code':
      return t('target.claudeCode')
    case 'vercel_agent_skills':
      return t('target.vercelAgentSkills')
    default:
      return key
  }
}

function badgeVariant(status: TargetStatus): 'secondary' | 'destructive' | 'outline' {
  switch (status) {
    case 'pass':
      return 'secondary'
    case 'error':
      return 'destructive'
    case 'warning':
    case 'unknown':
      return 'outline'
  }
}

export function PortableCompatibilityPanel({
  result,
  dense = false,
}: PortableCompatibilityPanelProps) {
  const t = useTranslations('skill.compatibility')
  const targets = parseTargets(result)

  return (
    <section className={dense ? 'space-y-2' : 'rounded-lg border border-border/70 p-3'}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">{t('title')}</h4>
        {targets.length > 0 ? (
          <span className="moldy-ui-micro text-muted-foreground">
            {t('targetCount', { count: targets.length })}
          </span>
        ) : null}
      </div>
      {targets.length === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">{t('empty')}</p>
      ) : (
        <div className="mt-2 space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {targets.map((target) => (
              <Badge key={target.key} variant={badgeVariant(target.status)}>
                {targetLabel(target.key, t)}
                <span className="text-muted-foreground">{t(`status.${target.status}`)}</span>
              </Badge>
            ))}
          </div>
          {targets.some((target) => target.issues.length > 0) ? (
            <ul className="space-y-1 text-xs text-muted-foreground">
              {targets.flatMap((target) =>
                target.issues.map((issue) => (
                  <li key={`${target.key}-${issue.message}`}>
                    {targetLabel(target.key, t)} · {issue.message}
                  </li>
                )),
              )}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">{t('noIssues')}</p>
          )}
        </div>
      )}
    </section>
  )
}
