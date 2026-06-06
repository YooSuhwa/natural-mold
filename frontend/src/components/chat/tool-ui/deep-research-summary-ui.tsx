'use client'

import { useMemo, useState } from 'react'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { useTranslations } from 'next-intl'
import {
  CheckCircleIcon,
  ChevronRightIcon,
  ExternalLinkIcon,
  Loader2Icon,
  SearchIcon,
} from 'lucide-react'
import {
  DEEP_RESEARCH_SUMMARY_TOOL_NAME,
  type DeepResearchSummary,
} from '@/lib/chat/deep-research-summary'
import { cn } from '@/lib/utils'

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function parseSummary(value: unknown): DeepResearchSummary | null {
  if (!value) return null
  if (typeof value === 'string') {
    try {
      return parseSummary(JSON.parse(value) as unknown)
    } catch {
      return null
    }
  }
  if (!isRecord(value)) return null
  if (!Array.isArray(value.searches)) return null
  return value as unknown as DeepResearchSummary
}

function formatDuration(durationMs: number | undefined): string | null {
  if (durationMs === undefined || durationMs < 1000) return null
  const totalSeconds = Math.round(durationMs / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  if (minutes <= 0) return `${seconds}s`
  return `${minutes}m ${seconds}s`
}

function domainInitial(domain: string): string {
  return (
    domain
      .replace(/^www\./, '')
      .charAt(0)
      .toUpperCase() || '?'
  )
}

function SourceBadges({ domains }: { domains: string[] }) {
  if (domains.length === 0) return null
  return (
    <div className="flex -space-x-1.5">
      {domains.slice(0, 3).map((domain) => (
        <span
          key={domain}
          title={domain}
          className="moldy-avatar-chip inline-flex size-6 items-center justify-center moldy-ui-micro font-semibold"
        >
          {domainInitial(domain)}
        </span>
      ))}
    </div>
  )
}

function DeepResearchSummaryCard({
  args,
  result,
  statusType,
}: {
  args: DeepResearchSummary
  result?: unknown
  statusType: string
}) {
  const t = useTranslations('chat.toolCall.deepResearch')
  const [expanded, setExpanded] = useState(false)
  const summary = useMemo(() => parseSummary(result) ?? args, [args, result])
  const complete = statusType === 'complete' || summary.completed_count >= summary.total_count
  const duration = formatDuration(summary.duration_ms)
  const meta = complete
    ? [t('completed'), t('sourceCount', { count: summary.source_count }), duration]
        .filter(Boolean)
        .join(' · ')
    : t('running', { count: summary.total_count })
  const domains = summary.domains ?? []

  return (
    <div className="my-3">
      <div className="moldy-chat-card">
        <button
          type="button"
          className="flex w-full items-center gap-3 px-4 py-3 text-left"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
        >
          <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
            {complete ? (
              <CheckCircleIcon className="size-4 text-status-success" />
            ) : (
              <Loader2Icon className="size-4 animate-spin text-status-info" />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium text-foreground">{summary.title}</div>
            <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
              <SourceBadges domains={domains} />
              <span className="truncate">{meta}</span>
            </div>
          </div>
          <ChevronRightIcon
            className={cn(
              'size-4 shrink-0 text-muted-foreground transition-transform duration-200',
              expanded && 'rotate-90',
            )}
          />
        </button>
        {expanded ? (
          <div className="border-t border-border/60 px-4 py-3">
            <div className="space-y-2">
              {summary.searches.map((search) => (
                <div
                  key={search.tool_call_id}
                  className="rounded-lg border border-border/50 bg-background px-3 py-2"
                >
                  <div className="flex min-w-0 items-center gap-2 text-xs">
                    <SearchIcon className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1 truncate font-medium text-foreground">
                      {search.query}
                    </span>
                    <span className="shrink-0 text-muted-foreground">
                      {t('resultCount', { count: search.result_count })}
                    </span>
                  </div>
                  {search.sources.length > 0 ? (
                    <div className="mt-2 space-y-1">
                      {search.sources.map((source) => (
                        <a
                          key={source.url}
                          href={source.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="flex min-w-0 items-center gap-1.5 moldy-ui-caption text-muted-foreground hover:text-primary-strong hover:underline"
                        >
                          <span className="truncate">{source.title || source.domain}</span>
                          <span className="shrink-0">· {source.domain}</span>
                          <ExternalLinkIcon className="size-3 shrink-0" />
                        </a>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}

export const DeepResearchSummaryToolUI = makeAssistantToolUI<DeepResearchSummary, unknown>({
  toolName: DEEP_RESEARCH_SUMMARY_TOOL_NAME,
  render: ({ args, result, status }) => (
    <DeepResearchSummaryCard args={args} result={result} statusType={status.type} />
  ),
})
