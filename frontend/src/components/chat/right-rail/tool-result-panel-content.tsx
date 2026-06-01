'use client'

import { useMemo } from 'react'
import { useTranslations } from 'next-intl'
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

type PreparedResult =
  | { kind: 'empty' }
  | { kind: 'image'; src: string }
  | { kind: 'link'; href: string }
  | { kind: 'json'; text: string }
  | { kind: 'text'; text: string }

function prepareResultValue(value: unknown): PreparedResult {
  if (value === null || value === undefined) return { kind: 'empty' }

  if (typeof value === 'string') {
    const trimmed = value.trim()

    if (isImageUrl(trimmed)) {
      return { kind: 'image', src: trimmed }
    }

    if (isUrl(trimmed)) {
      return { kind: 'link', href: trimmed }
    }

    const looksLikeJson =
      (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
      (trimmed.startsWith('[') && trimmed.endsWith(']'))
    const parsedJson = looksLikeJson ? safeParseJson(trimmed) : null
    if (parsedJson !== null) {
      return { kind: 'json', text: JSON.stringify(parsedJson, null, 2) }
    }

    return { kind: 'text', text: value }
  }

  return { kind: 'json', text: JSON.stringify(value, null, 2) }
}

/** Best-effort pretty rendering for tool args/result values. */
function ResultRenderer({ value }: { value: unknown }) {
  const t = useTranslations('chat.rightRail')
  const prepared = useMemo(() => prepareResultValue(value), [value])

  if (prepared.kind === 'empty') {
    return <p className="text-xs text-muted-foreground">{t('noResult')}</p>
  }

  if (prepared.kind === 'image') {
    return <ChatImage src={prepared.src} alt="tool result" />
  }

  if (prepared.kind === 'link') {
    return (
      <a
        href={prepared.href}
        target="_blank"
        rel="noreferrer noopener"
        className="break-all text-xs text-primary-strong underline hover:opacity-80"
      >
        {prepared.href}
      </a>
    )
  }

  if (prepared.kind === 'text') {
    return (
      <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-md border border-border/60 bg-card p-3 text-xs leading-relaxed text-foreground/90">
        {prepared.text}
      </pre>
    )
  }

  return (
    <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-all rounded-md border border-border/60 bg-card p-3 font-mono text-[11px] leading-relaxed text-foreground/90">
      {prepared.text}
    </pre>
  )
}

function StatusBadge({ status }: { status: ToolResultPayload['status'] }) {
  const t = useTranslations('chat.rightRail.status')
  if (!status) return null

  const config = {
    running: {
      Icon: Loader2Icon,
      label: t('running'),
      className: 'bg-status-warn/10 text-status-warn',
      iconClassName: 'animate-spin',
    },
    complete: {
      Icon: CheckCircle2Icon,
      label: t('complete'),
      className: 'bg-status-success/10 text-status-success',
      iconClassName: '',
    },
    incomplete: {
      Icon: AlertCircleIcon,
      label: t('incomplete'),
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
  const t = useTranslations('chat.rightRail')
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
            {t('arguments')}
          </h3>
          <ResultRenderer value={payload.args} />
        </section>
      ) : null}

      <section>
        <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {t('result')}
        </h3>
        <ResultRenderer value={payload.result} />
      </section>

      <p className="text-[10px] text-muted-foreground/70">tool_call_id: {payload.toolCallId}</p>
    </div>
  )
}
