'use client'

import {
  useMessages,
  useToolCalls,
  type AnyStream,
  type SubagentDiscoverySnapshot,
} from '@langchain/react'
import type { BaseMessage } from '@langchain/core/messages'
import { PanelRightOpenIcon } from 'lucide-react'
import { useAtomValue, useSetAtom } from 'jotai'
import { useTranslations } from 'next-intl'
import { CollapsiblePill, type PillStatus } from '@/components/chat/tool-ui/collapsible-pill'
import { useChatConversationId } from '@/components/chat/conversation-context'
import {
  useSubagentInlinePolicy,
  useSubagentSnapshot,
  useSubagentStream,
} from '@/lib/chat/langgraph-runtime/subagent-runtime'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import { chatSubagentNamesAtom, resolveSubagentDisplayName } from '@/lib/stores/chat-subagent-names'

export interface SubagentCardFallback {
  readonly agentName: string
  readonly input: string
  readonly status: PillStatus
}

interface SubagentCardProps {
  readonly fallback: SubagentCardFallback
  readonly toolCallId: string
  readonly turnToolCallIds?: readonly string[]
}

const SNAPSHOT_STATUS: Record<SubagentDiscoverySnapshot['status'], PillStatus> = {
  running: 'loading',
  complete: 'success',
  error: 'error',
}

function formatUnknown(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return String(value)
  }
  return JSON.stringify(value)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function safeDisplaySummary(part: Record<string, unknown>): string {
  if (typeof part.summary === 'string') return part.summary
  if (typeof part.status === 'string') return part.status
  return ''
}

function textFromPart(part: unknown): string {
  if (typeof part === 'string') return part
  if (!isRecord(part)) return ''

  const type = typeof part.type === 'string' ? part.type : undefined
  if (type === 'reasoning' || type === 'thinking') return safeDisplaySummary(part)
  if (type === 'text' && typeof part.text === 'string') return part.text
  return safeDisplaySummary(part)
}

function formatMessage(message: BaseMessage): string {
  const content = message.content
  if (typeof content === 'string') return content
  if (Array.isArray(content)) return content.map(textFromPart).filter(Boolean).join('')
  return ''
}

function scopedMessageKey(message: BaseMessage, subagentId: string, index: number): string {
  return `${message.id ?? `${subagentId}-message`}:${index}`
}

const OUTPUT_SUMMARY_MAX_CHARS = 140

/** 완료된 서브에이전트의 output 첫 의미 줄 — 접힌 pill에서도 결과가 보이게. */
function outputSummaryLine(output: string): string {
  const line = output
    .split('\n')
    .map((part) => part.trim())
    .find(Boolean)
  if (!line) return ''
  return line.length > OUTPUT_SUMMARY_MAX_CHARS
    ? `${line.slice(0, OUTPUT_SUMMARY_MAX_CHARS)}…`
    : line
}

function SubagentHeaderMeta({
  input,
  namespace,
  summary,
}: {
  readonly input: string
  readonly namespace: readonly string[] | undefined
  readonly summary?: string
}) {
  const namespaceLabel = namespace?.join('/')
  const lead = summary || input
  return (
    <span className="flex min-w-0 items-center gap-1 overflow-hidden">
      {lead ? (
        <span
          className={summary ? 'truncate text-foreground/80' : 'truncate'}
          data-moldy-subagent-summary={summary ? 'true' : undefined}
        >
          {lead}
        </span>
      ) : null}
      {lead && namespaceLabel ? <span aria-hidden>·</span> : null}
      {namespaceLabel ? <span className="shrink-0 font-mono">{namespaceLabel}</span> : null}
    </span>
  )
}

function SubagentDetails({
  stream,
  subagent,
}: {
  readonly stream: AnyStream
  readonly subagent: SubagentDiscoverySnapshot
}) {
  const t = useTranslations('chat.toolUi.subAgent')
  const messages = useMessages(stream, subagent)
  const toolCalls = useToolCalls(stream, subagent)
  const output = formatUnknown(subagent.output)
  const hasDetails = messages.length > 0 || toolCalls.length > 0 || output || subagent.error

  return (
    <div className="space-y-2 border-t border-border/60 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2 moldy-ui-micro text-muted-foreground">
        <span>{t('messageCount', { count: messages.length })}</span>
        <span aria-hidden>·</span>
        <span>{t('toolCount', { count: toolCalls.length })}</span>
      </div>
      {subagent.error ? (
        <p className="rounded-md border border-status-danger/30 bg-status-danger/5 px-2 py-1.5 text-xs text-status-danger">
          {subagent.error}
        </p>
      ) : null}
      {messages.length > 0 ? (
        <div className="space-y-1">
          {messages.map((message, index) => (
            <p
              key={scopedMessageKey(message, subagent.id, index)}
              className="rounded-md bg-muted/45 px-2 py-1.5 text-xs leading-relaxed text-foreground/85"
            >
              {formatMessage(message)}
            </p>
          ))}
        </div>
      ) : null}
      {toolCalls.length > 0 ? (
        <div className="flex flex-wrap gap-1">
          {toolCalls.map((toolCall) => (
            <span
              key={toolCall.callId}
              className="rounded-md border border-border/60 bg-background px-2 py-1 font-mono moldy-ui-micro text-muted-foreground"
            >
              {toolCall.name}
            </span>
          ))}
        </div>
      ) : null}
      {output ? (
        <p className="line-clamp-3 rounded-md border border-border/60 bg-card px-2 py-1.5 text-xs text-muted-foreground">
          {output}
        </p>
      ) : null}
      {!hasDetails ? (
        <p className="rounded-md border border-dashed border-border/60 bg-muted/30 px-2 py-1.5 text-xs text-muted-foreground">
          {t('waiting')}
        </p>
      ) : null}
    </div>
  )
}

export function SubagentCard({ fallback, toolCallId, turnToolCallIds }: SubagentCardProps) {
  const t = useTranslations('chat.toolUi.subAgent')
  const setRail = useSetAtom(chatRightRailAtom)
  const conversationId = useChatConversationId()
  const subagent = useSubagentSnapshot(toolCallId)
  const inlinePolicy = useSubagentInlinePolicy(toolCallId, turnToolCallIds)
  const stream = useSubagentStream()
  const subagentNames = useAtomValue(chatSubagentNamesAtom)
  const rawAgentName = subagent?.name ?? fallback.agentName
  const agentName = resolveSubagentDisplayName(
    conversationId ? subagentNames[conversationId] : undefined,
    rawAgentName,
  )
  const input = subagent?.taskInput ?? fallback.input
  const status = subagent ? SNAPSHOT_STATUS[subagent.status] : fallback.status
  const outputSummary =
    subagent?.status === 'complete' ? outputSummaryLine(formatUnknown(subagent.output)) : ''
  const canRenderScopedDetails =
    subagent !== null && stream !== null && inlinePolicy.canRenderInlineDetails
  const openRail = () =>
    setRail({
      mode: 'subagent',
      subagent: { conversationId, toolCallId, agentName, input },
    })

  return (
    <CollapsiblePill
      kind="subagent"
      status={status}
      title={agentName}
      meta={
        <SubagentHeaderMeta
          input={input || t('invocation')}
          namespace={subagent?.namespace}
          summary={outputSummary}
        />
      }
      defaultExpanded={canRenderScopedDetails ? inlinePolicy.defaultExpanded : false}
      onClick={canRenderScopedDetails ? undefined : openRail}
      renderBody={
        canRenderScopedDetails
          ? () => <SubagentDetails stream={stream} subagent={subagent} />
          : undefined
      }
      trailing={
        <button
          type="button"
          className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label={t('openDetails')}
          onClick={(event) => {
            event.stopPropagation()
            openRail()
          }}
        >
          <PanelRightOpenIcon className="size-3.5" />
        </button>
      }
    />
  )
}
