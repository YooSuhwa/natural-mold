'use client'

import { useState } from 'react'
import {
  ChevronDownIcon,
  WrenchIcon,
  Loader2Icon,
  CircleCheckIcon,
  PanelRightOpenIcon,
} from 'lucide-react'
import { useSetAtom } from 'jotai'
import { useTranslations } from 'next-intl'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { cn } from '@/lib/utils'
import { ChatImage } from '@/components/chat/markdown-content'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'

// ──────────────────────────────────────────────
// ToolFallbackPanel — 확장 가능 도구 패널
// ──────────────────────────────────────────────

interface ToolFallbackPanelProps {
  toolName: string
  args: Record<string, unknown>
  result?: unknown
  status: 'running' | 'complete' | 'error'
  toolCallId?: string
}

/** 도구 결과 JSON에서 이미지 URL을 추출 */
function extractImageUrls(data: unknown): string[] {
  const urls: string[] = []
  if (!data) return urls

  const walk = (obj: unknown) => {
    if (!obj || typeof obj !== 'object') return
    if (Array.isArray(obj)) {
      obj.forEach(walk)
      return
    }
    for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
      if (
        typeof value === 'string' &&
        (key.toLowerCase().includes('image') || key.toLowerCase().includes('img')) &&
        (value.startsWith('http') || value.startsWith('/api/'))
      ) {
        urls.push(value)
      } else if (typeof value === 'object') {
        walk(value)
      }
    }
  }

  // 도구 결과가 JSON 문자열로 전달되는 경우 파싱 시도 (일부 MCP/HTTP 도구)
  if (typeof data === 'string') {
    try {
      walk(JSON.parse(data))
    } catch {
      // not JSON
    }
  } else if (typeof data === 'object') {
    walk(data)
  }

  // MCP 도구 결과: [{type:'text', text:'JSON문자열'}] 형태 처리
  if (Array.isArray(data)) {
    for (const item of data) {
      if (
        item &&
        typeof item === 'object' &&
        'text' in item &&
        typeof (item as Record<string, unknown>).text === 'string'
      ) {
        try {
          walk(JSON.parse((item as Record<string, unknown>).text as string))
        } catch {
          // not JSON
        }
      }
    }
  }

  return urls
}

export function ToolFallbackPanel({
  toolName,
  args,
  result,
  status,
  toolCallId,
}: ToolFallbackPanelProps) {
  const [expanded, setExpanded] = useState(false)
  const t = useTranslations('chat.toolCall')
  const setRail = useSetAtom(chatRightRailAtom)
  const hasArgs = args && Object.keys(args).length > 0
  const hasResult = result !== undefined && result !== null
  const imageUrls = hasResult ? extractImageUrls(result) : []
  const railStatus =
    status === 'running' ? 'running' : status === 'complete' ? 'complete' : 'incomplete'

  const handleExpandToPanel = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!toolCallId) return
    setRail({
      mode: 'tool-result',
      toolResult: {
        toolCallId,
        toolName,
        args,
        result,
        status: railStatus,
      },
    })
  }

  return (
    <div
      className={cn(
        'w-full rounded-xl border border-border/50 text-xs transition-colors',
        status === 'error' && 'border-destructive/20 bg-destructive/5',
      )}
    >
      <div className="flex w-full items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          onClick={() => setExpanded(!expanded)}
        >
          {status === 'running' ? (
            <Loader2Icon className="size-3.5 shrink-0 animate-spin text-primary-strong" />
          ) : (
            <CircleCheckIcon className="size-3.5 shrink-0 text-emerald-500" />
          )}
          <WrenchIcon className="size-3 shrink-0 text-muted-foreground" />
          <span className="truncate font-medium">{toolName}</span>
          <span className="shrink-0 text-muted-foreground">
            {status === 'running' ? t('calling') : t('completed')}
          </span>
        </button>
        {toolCallId ? (
          <button
            type="button"
            onClick={handleExpandToPanel}
            aria-label="Expand to panel"
            title="Expand to panel"
            className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <PanelRightOpenIcon className="size-3.5" aria-hidden />
          </button>
        ) : null}
        {(hasArgs || hasResult) && (
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            aria-label={expanded ? 'Collapse' : 'Expand'}
            className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          >
            <ChevronDownIcon
              className={cn(
                'size-3.5 transition-transform duration-200',
                expanded && 'rotate-180',
              )}
            />
          </button>
        )}
      </div>

      {/* 이미지 URL이 있으면 패널 바깥에 바로 표시 */}
      {imageUrls.length > 0 && (
        <div className="px-3 pb-2 space-y-2">
          {imageUrls.map((url) => (
            <ChatImage key={url} src={url} alt={toolName} />
          ))}
        </div>
      )}

      {expanded && (
        <div className="space-y-2 px-3 pb-3">
          {hasArgs && (
            <div className="rounded-lg border border-border/40 bg-background p-2.5">
              <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                {t('parameters')}
              </div>
              <pre className="whitespace-pre-wrap break-all font-mono text-[11px] text-foreground/80">
                {JSON.stringify(args, null, 2)}
              </pre>
            </div>
          )}
          {hasResult && (
            <div className="rounded-lg border border-border/40 bg-background p-2.5">
              <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                {t('results')}
              </div>
              <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] text-foreground/80">
                {typeof result === 'string' ? result : JSON.stringify(result, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ──────────────────────────────────────────────
// GenericToolFallback — 미등록 도구용 폴백 UI
// ──────────────────────────────────────────────

function resolveStatus(statusType: string): 'running' | 'complete' | 'error' {
  if (statusType === 'running') return 'running'
  if (statusType === 'complete') return 'complete'
  return 'error'
}

/** 등록되지 않은 도구를 위한 폴백 UI. makeAssistantToolUI로 등록. */
export const GenericToolFallback = makeAssistantToolUI({
  toolName: '*',
  render: ({ toolName, args, result, status, toolCallId }) => (
    <ToolFallbackPanel
      toolName={toolName}
      args={args as Record<string, unknown>}
      result={result}
      status={resolveStatus(status.type)}
      toolCallId={toolCallId}
    />
  ),
})
