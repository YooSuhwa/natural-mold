'use client'

import {
  useMessages,
  useToolCalls,
  type AnyStream,
  type SubagentDiscoverySnapshot,
} from '@langchain/react'
import type { BaseMessage } from '@langchain/core/messages'
import { useTranslations } from 'next-intl'
import { useAtomValue } from 'jotai'
import { useSharedSubagentRuntime } from '@/lib/chat/langgraph-runtime/subagent-runtime'
import type { SubagentPayload } from '@/lib/stores/chat-right-rail'
import { chatSubagentNamesAtom, resolveSubagentDisplayName } from '@/lib/stores/chat-subagent-names'

import { useChildTasks } from '@/lib/hooks/use-child-tasks'
import { AgUiSubagentDetails } from '../ag-ui-subagent-details'
import { ManagedChildTask } from './managed-child-task'

interface Props {
  payload: SubagentPayload
}

export function SubagentPanelContent({ payload }: Props) {
  if (payload.conversationId && payload.childTaskId) {
    return <ManagedChildTask conversationId={payload.conversationId} taskId={payload.childTaskId} />
  }
  return payload.conversationId ? (
    <LegacyChildPanel conversationId={payload.conversationId} payload={payload} />
  ) : (
    <StreamSubagentPanel payload={payload} />
  )
}

function LegacyChildPanel({ conversationId, payload }: Props & { conversationId: string }) {
  const t = useTranslations('chat.childTasks')
  const list = useChildTasks(conversationId)
  if (list.isPending) return <p role="status">{t('loading')}</p>
  if (!list.allowLegacyDetails) return <p role="status">{t('unavailable')}</p>
  return <StreamSubagentPanel payload={payload} />
}

function StreamSubagentPanel({ payload }: Props) {
  const t = useTranslations('chat.rightRail')
  const runtime = useSharedSubagentRuntime(payload.conversationId)
  const selected = payload.subagentRunId
    ? runtime?.subagentsByRunId?.get(payload.subagentRunId)
    : null
  const projected = selected?.id === payload.toolCallId ? selected : null
  const subagent = projected ?? runtime?.subagentsByToolCallId.get(payload.toolCallId) ?? null
  const subagentNames = useAtomValue(chatSubagentNamesAtom)
  const tChildren = useTranslations('chat.childTasks')
  if (
    (payload.subagentRunId || runtime?.subagentsByRunId) &&
    (!payload.conversationId || !projected)
  ) {
    return <p role="status">{tChildren('unavailable')}</p>
  }
  const agentName = resolveSubagentDisplayName(
    payload.conversationId ? subagentNames[payload.conversationId] : undefined,
    subagent?.name ?? payload.agentName,
  )
  const input = subagent?.taskInput ?? payload.input
  const hasInput = Boolean(input && input.trim().length > 0)

  return (
    <div className="space-y-4">
      <section>
        <h3 className="mb-2 moldy-ui-micro font-semibold uppercase tracking-wider text-muted-foreground">
          {t('agent')}
        </h3>
        <p className="text-sm font-medium text-foreground">{agentName}</p>
        {subagent ? (
          <p className="mt-1 font-mono moldy-ui-micro text-muted-foreground">
            {subagent.namespace.join('/')}
          </p>
        ) : null}
      </section>

      {hasInput ? (
        <section>
          <h3 className="mb-2 moldy-ui-micro font-semibold uppercase tracking-wider text-muted-foreground">
            {t('input')}
          </h3>
          <pre className="whitespace-pre-wrap break-words rounded-md border border-border/60 bg-card p-3 text-xs leading-relaxed text-foreground/90">
            {input}
          </pre>
        </section>
      ) : null}

      <section>
        <h3 className="mb-2 moldy-ui-micro font-semibold uppercase tracking-wider text-muted-foreground">
          {t('output')}
        </h3>
        {projected ? (
          <AgUiSubagentDetails child={projected} />
        ) : runtime?.stream && subagent ? (
          <ScopedSubagentDetails stream={runtime.stream} subagent={subagent} />
        ) : (
          <p className="rounded-md border border-dashed border-border/60 bg-muted/40 p-3 text-xs text-muted-foreground">
            {t('subagentPending')}
          </p>
        )}
      </section>

      <p className="moldy-ui-micro text-muted-foreground/70">tool_call_id: {payload.toolCallId}</p>
    </div>
  )
}

function formatUnknown(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean' || typeof value === 'bigint') {
    return String(value)
  }
  return JSON.stringify(value)
}

function textFromPart(part: unknown): string {
  if (typeof part === 'string') return part
  if (typeof part !== 'object' || part === null || Array.isArray(part)) return formatUnknown(part)
  if ('text' in part && typeof part.text === 'string') return part.text
  if ('content' in part) return formatUnknown(part.content)
  return formatUnknown(part)
}

function formatMessage(message: BaseMessage): string {
  const content = message.content
  if (typeof content === 'string') return content
  if (Array.isArray(content)) return content.map(textFromPart).filter(Boolean).join('')
  return formatUnknown(content)
}

function scopedMessageKey(message: BaseMessage, subagentId: string, index: number): string {
  return `${message.id ?? `${subagentId}-message`}:${index}`
}

function ScopedSubagentDetails({
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
  const hasDetails = Boolean(
    messages.length > 0 || toolCalls.length > 0 || output || subagent.error,
  )

  return (
    <div className="space-y-2">
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
        <p className="line-clamp-4 rounded-md border border-border/60 bg-card px-2 py-1.5 text-xs text-muted-foreground">
          {output}
        </p>
      ) : null}
      {!hasDetails ? (
        <p className="rounded-md border border-dashed border-border/60 bg-muted/40 p-3 text-xs text-muted-foreground">
          {t('waiting')}
        </p>
      ) : null}
    </div>
  )
}
