'use client'

import { CircleAlertIcon, CircleCheckIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'

import type { WorkflowIssue } from '@/lib/types/workflow'

/** The "확인할 것" list: every server check result; a node issue selects its node. */
export function IssuesList({
  issues,
  onSelect,
}: {
  issues: WorkflowIssue[]
  onSelect: (nodeId: string) => void
}) {
  const t = useTranslations('workflows.editor')
  if (issues.length === 0) {
    return (
      <p className="flex items-center gap-2 text-sm text-status-success">
        <CircleCheckIcon className="size-4" aria-hidden />
        {t('noIssues')}
      </p>
    )
  }
  return (
    <ul className="flex flex-col gap-1" aria-label={t('issuesTitle')}>
      {issues.map((issue, index) => (
        <li key={`${issue.code}:${issue.node_id ?? ''}:${index}`}>
          <button
            type="button"
            disabled={!issue.node_id}
            className="hover:bg-accent focus-visible:bg-accent disabled:cursor-default flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-sm"
            onClick={() => issue.node_id && onSelect(issue.node_id)}
          >
            <CircleAlertIcon className="mt-0.5 size-4 shrink-0 text-status-danger" aria-hidden />
            <span className="min-w-0">
              {issue.node_id ? (
                <span className="block text-xs text-muted-foreground">{issue.node_id}</span>
              ) : null}
              <span className="block">{issue.message}</span>
            </span>
          </button>
        </li>
      ))}
    </ul>
  )
}
