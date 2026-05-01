'use client'

import { Loader2Icon, CheckCircle2Icon, AlertCircleIcon, WrenchIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { ChatImage } from '@/components/chat/markdown-content'
import type { ToolResultPayload } from '@/lib/stores/chat-right-rail'

interface Props {
  payload: ToolResultPayload
}

const URL_PATTERN = /^https?:\/\/[^\s]+$/i

function isUrl(value: string): boolean {
  return URL_PATTERN.test(value.trim())
}

function isImageUrl(value: string): boolean {
  if (!isUrl(value)) return false
  return /\.(png|jpe?g|gif|webp|svg|avif)(\?.*)?$/i.test(value.trim())
}

function safeParseJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

/** Best-effort pretty rendering for tool args/result values. */
function ResultRenderer({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <p className="text-xs text-muted-foreground">No result.</p>
  }

  if (typeof value === 'string') {
    const trimmed = value.trim()

    // 이미지 URL이면 인라인 표시
    if (isImageUrl(trimmed)) {
      return <ChatImage src={trimmed} alt="tool result" />
    }

    // 일반 URL이면 링크
    if (isUrl(trimmed)) {
      return (
        <a
          href={trimmed}
          target="_blank"
          rel="noreferrer noopener"
          className="break-all text-xs text-primary-strong underline hover:opacity-80"
        >
          {trimmed}
        </a>
      )
    }

    // JSON 문자열이면 파싱해서 pretty
    const looksLikeJson =
      (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
      (trimmed.startsWith('[') && trimmed.endsWith(']'))
    const parsedJson = looksLikeJson ? safeParseJson(trimmed) : null
    if (parsedJson !== null) {
      return (
        <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-all rounded-md border border-border/60 bg-card p-3 font-mono text-[11px] leading-relaxed text-foreground/90">
          {JSON.stringify(parsedJson, null, 2)}
        </pre>
      )
    }

    return (
      <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-md border border-border/60 bg-card p-3 text-xs leading-relaxed text-foreground/90">
        {value}
      </pre>
    )
  }

  // object / array → JSON pretty
  return (
    <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-all rounded-md border border-border/60 bg-card p-3 font-mono text-[11px] leading-relaxed text-foreground/90">
      {JSON.stringify(value, null, 2)}
    </pre>
  )
}

function StatusBadge({ status }: { status: ToolResultPayload['status'] }) {
  if (!status) return null

  const config = {
    running: {
      Icon: Loader2Icon,
      label: 'Running',
      className: 'bg-status-warn/10 text-status-warn',
      iconClassName: 'animate-spin',
    },
    complete: {
      Icon: CheckCircle2Icon,
      label: 'Complete',
      className: 'bg-status-success/10 text-status-success',
      iconClassName: '',
    },
    incomplete: {
      Icon: AlertCircleIcon,
      label: 'Incomplete',
      className: 'bg-status-danger/10 text-status-danger',
      iconClassName: '',
    },
  }[status]

  if (!config) return null
  const { Icon, label, className, iconClassName } = config

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium',
        className,
      )}
    >
      <Icon className={cn('size-3', iconClassName)} aria-hidden />
      {label}
    </span>
  )
}

export function ToolResultPanelContent({ payload }: Props) {
  const hasArgs =
    payload.args !== undefined &&
    payload.args !== null &&
    !(typeof payload.args === 'object' && Object.keys(payload.args as object).length === 0)

  return (
    <div className="space-y-4">
      <section className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <WrenchIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <p className="truncate text-sm font-medium text-foreground">{payload.toolName}</p>
        </div>
        <StatusBadge status={payload.status} />
      </section>

      {hasArgs ? (
        <section>
          <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Arguments
          </h3>
          <ResultRenderer value={payload.args} />
        </section>
      ) : null}

      <section>
        <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          Result
        </h3>
        <ResultRenderer value={payload.result} />
      </section>

      <p className="text-[10px] text-muted-foreground/70">tool_call_id: {payload.toolCallId}</p>
    </div>
  )
}
