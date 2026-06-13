'use client'

import { useTranslations } from 'next-intl'
import {
  useSubagentProgressSummary,
  type SubagentProgressSummary,
} from '@/lib/chat/langgraph-runtime/subagent-runtime'

interface SubagentProgressProps {
  readonly summary?: SubagentProgressSummary
  readonly toolCallIds?: readonly string[]
}

function SubagentProgressContent({ summary }: { readonly summary: SubagentProgressSummary }) {
  const t = useTranslations('chat.subagents')
  const resolved = summary
  if (resolved.total === 0) return null

  const settled = resolved.completed + resolved.failed

  return (
    <div className="moldy-tool-pill flex items-center gap-2 px-3 py-2 text-xs">
      <span className="min-w-0 flex-1 truncate font-medium">
        {t('progress', { completed: settled, total: resolved.total })}
      </span>
      {resolved.running > 0 ? (
        <span className="shrink-0 text-muted-foreground">
          {t('running', { count: resolved.running })}
        </span>
      ) : null}
      {resolved.failed > 0 ? (
        <span className="shrink-0 text-status-danger">
          {t('failed', { count: resolved.failed })}
        </span>
      ) : null}
      <progress
        aria-label={t('progressLabel')}
        className="h-1.5 w-20 shrink-0 accent-primary"
        max={resolved.total}
        value={settled}
      />
    </div>
  )
}

function RuntimeSubagentProgress({ toolCallIds }: { readonly toolCallIds?: readonly string[] }) {
  const runtimeSummary = useSubagentProgressSummary(toolCallIds)
  return <SubagentProgressContent summary={runtimeSummary} />
}

export function SubagentProgress({ summary, toolCallIds }: SubagentProgressProps) {
  if (summary) return <SubagentProgressContent summary={summary} />
  return <RuntimeSubagentProgress toolCallIds={toolCallIds} />
}
