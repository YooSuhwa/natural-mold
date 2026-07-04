'use client'

import { useMemo } from 'react'
import { PanelRightOpenIcon, PlugIcon } from 'lucide-react'
import { useSetAtom } from 'jotai'
import { useTranslations } from 'next-intl'
import { makeAssistantToolUI } from '@assistant-ui/react'
import { CollapsiblePill, pillStatusFromAssistantUi } from './collapsible-pill'
import { useIsToolGroupChild } from './tool-group-child-context'
import { useMcpToolServer, useToolIcon } from './tool-icon-context'
import { ChatImage } from '@/components/chat/chat-image'
import { useChatConversationId } from '@/components/chat/conversation-context'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import { toolCallChildLabel } from '@/lib/chat/tool-group-meta'

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
  const seen = new WeakSet<object>()

  const walk = (obj: unknown) => {
    if (!obj || typeof obj !== 'object') return
    if (seen.has(obj)) return
    seen.add(obj)
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

function formatToolValue(value: unknown, serializeFailed: string): string {
  if (typeof value === 'string') return value

  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return serializeFailed
  }
}

function ToolFallbackBody({
  toolName,
  args,
  result,
  hasArgs,
  hasResult,
}: {
  toolName: string
  args: Record<string, unknown>
  result?: unknown
  hasArgs: boolean
  hasResult: boolean
}) {
  const t = useTranslations('chat.toolCall')
  const serializeFailed = t('serializeFailed')
  const argsText = useMemo(
    () => (hasArgs ? formatToolValue(args, serializeFailed) : ''),
    [args, hasArgs, serializeFailed],
  )
  const resultText = useMemo(
    () => (hasResult ? formatToolValue(result, serializeFailed) : ''),
    [hasResult, result, serializeFailed],
  )
  const imageUrls = useMemo(() => (hasResult ? extractImageUrls(result) : []), [hasResult, result])

  return (
    <div className="space-y-2">
      {hasArgs && (
        <div className="rounded-lg border border-border/40 bg-background p-2.5">
          <div className="mb-1.5 moldy-ui-micro font-semibold uppercase tracking-wider text-muted-foreground">
            {t('parameters')}
          </div>
          <pre className="whitespace-pre-wrap break-all font-mono moldy-ui-caption text-foreground/80">
            {argsText}
          </pre>
        </div>
      )}
      {hasResult && (
        <div className="rounded-lg border border-border/40 bg-background p-2.5">
          <div className="mb-1.5 moldy-ui-micro font-semibold uppercase tracking-wider text-muted-foreground">
            {t('results')}
          </div>
          <pre className="max-h-60 overflow-auto whitespace-pre-wrap break-all font-mono moldy-ui-caption text-foreground/80">
            {resultText}
          </pre>
        </div>
      )}
      {imageUrls.length > 0 && (
        <div className="space-y-2">
          {imageUrls.map((url) => (
            <ChatImage key={url} src={url} alt={toolName} />
          ))}
        </div>
      )}
    </div>
  )
}

export function ToolFallbackPanel({
  toolName,
  args,
  result,
  status,
  toolCallId,
}: ToolFallbackPanelProps) {
  const t = useTranslations('chat.toolCall')
  const setRail = useSetAtom(chatRightRailAtom)
  const conversationId = useChatConversationId()
  const leadingIcon = useToolIcon(toolName)
  const mcpServerName = useMcpToolServer(toolName)
  // 그룹 자식이면 도구명(그룹 헤더에 이미 있음) 대신 호출별 인자/결과 요약을 제목으로.
  const isGroupChild = useIsToolGroupChild()
  const pillTitle = (isGroupChild ? toolCallChildLabel(args, result) : null) ?? toolName
  const hasArgs = args && Object.keys(args).length > 0
  const hasResult = result !== undefined && result !== null
  const railStatus =
    status === 'running' ? 'running' : status === 'complete' ? 'complete' : 'incomplete'

  const handleExpandToPanel = (e: React.MouseEvent) => {
    e.stopPropagation()
    if (!toolCallId) return
    setRail({
      mode: 'tool-result',
      toolResult: {
        conversationId,
        toolCallId,
        toolName,
        args,
        result,
        status: railStatus,
      },
    })
  }

  const trailing = toolCallId ? (
    <button
      type="button"
      onClick={handleExpandToPanel}
      aria-label={t('expandToPanel')}
      title={t('expandToPanel')}
      className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
    >
      <PanelRightOpenIcon className="size-3.5" aria-hidden />
    </button>
  ) : null

  return (
    <div className="space-y-2">
      <CollapsiblePill
        kind="tool"
        leadingIcon={leadingIcon}
        status={pillStatusFromAssistantUi(status)}
        title={pillTitle}
        meta={
          mcpServerName ? (
            <span className="flex min-w-0 items-center gap-1.5">
              <span
                className="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-status-info/15 px-1.5 py-0.5 moldy-ui-micro font-medium text-status-info"
                data-moldy-mcp-server={mcpServerName}
              >
                <PlugIcon className="size-2.5" aria-hidden />
                <span className="max-w-24 truncate">{mcpServerName}</span>
              </span>
              <span className="truncate">
                {status === 'running' ? t('calling') : t('completed')}
              </span>
            </span>
          ) : status === 'running' ? (
            t('calling')
          ) : (
            t('completed')
          )
        }
        trailing={trailing}
        renderBody={
          hasArgs || hasResult
            ? () => (
                <ToolFallbackBody
                  toolName={toolName}
                  args={args}
                  result={result}
                  hasArgs={hasArgs}
                  hasResult={hasResult}
                />
              )
            : undefined
        }
      />
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
