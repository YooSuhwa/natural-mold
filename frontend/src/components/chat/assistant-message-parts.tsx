'use client'

import type { ReactNode } from 'react'
import { MessagePrimitive, useAuiState, type EnrichedPartState } from '@assistant-ui/react'
import { StreamdownTextPrimitive } from '@assistant-ui/react-streamdown'
import { math } from '@streamdown/math'
import { buildMarkdownComponents } from '@/components/chat/markdown-components'
import { CHAT_STREAMING_REMARK_PLUGINS } from '@/components/chat/markdown-streaming-plugins'
import { GenericToolFallback } from '@/components/chat/tool-ui/generic-tool-ui'
import { ToolGroupContainer } from '@/components/chat/tool-ui/tool-group-container'
import { GroupedApprovalCard } from '@/components/chat/tool-ui/grouped-approval-card'
import {
  groupAssistantParts,
  groupToolName,
  isGroupToolNode,
  type GroupedRenderInfo,
} from '@/lib/chat/group-assistant-parts'

const STREAMDOWN_PLUGINS = { math }
const MARKDOWN_COMPONENTS_STREAMING = buildMarkdownComponents({ isStreaming: true })
const MARKDOWN_COMPONENTS_FINAL = buildMarkdownComponents({ isStreaming: false })

function AssistantTextPart() {
  const isRunning = useAuiState(
    (s) => (s.message?.status as { type?: string } | undefined)?.type === 'running',
  )
  const components = isRunning ? MARKDOWN_COMPONENTS_STREAMING : MARKDOWN_COMPONENTS_FINAL
  return (
    <div
      className="prose-chat py-1 text-sm leading-relaxed text-foreground"
      data-chat-quote-text
      data-chat-streaming={isRunning ? 'true' : undefined}
    >
      <StreamdownTextPrimitive
        plugins={STREAMDOWN_PLUGINS}
        remarkPlugins={CHAT_STREAMING_REMARK_PLUGINS}
        caret={isRunning ? 'block' : undefined}
        components={components as never}
      />
    </div>
  )
}

function ToolCallFallback({
  toolCallId,
  toolName,
  args,
  result,
  status,
}: {
  readonly toolCallId: string
  readonly toolName: string
  readonly args: Record<string, unknown>
  readonly result?: unknown
  readonly status: { readonly type: string }
}) {
  return (
    <GenericToolFallback
      toolCallId={toolCallId}
      toolName={toolName}
      args={args}
      result={result}
      status={status}
    />
  )
}

function OrderedTextPart() {
  return (
    <div className="order-2">
      <AssistantTextPart />
    </div>
  )
}

function OrderedToolCall({
  toolUI,
  toolCallId,
  toolName,
  args,
  result,
  status,
}: {
  readonly toolUI: ReactNode
  readonly toolCallId: string
  readonly toolName: string
  readonly args: Record<string, unknown>
  readonly result?: unknown
  readonly status: { readonly type: string }
}) {
  return (
    <div className="order-1">
      {toolUI ?? (
        <ToolCallFallback
          toolCallId={toolCallId}
          toolName={toolName}
          args={args}
          result={result}
          status={status}
        />
      )}
    </div>
  )
}

export { groupAssistantParts }

export function renderGroupedAssistantPart({ part, children }: GroupedRenderInfo): ReactNode {
  if (isGroupToolNode(part)) {
    const running = part.status?.type === 'running'
    if (part.indices.length < 2) {
      return <div className="order-1">{children}</div>
    }
    if (groupToolName(part) === 'request_approval') {
      return (
        <div className="order-1">
          <GroupedApprovalCard count={part.indices.length}>{children}</GroupedApprovalCard>
        </div>
      )
    }
    return (
      <div className="order-1">
        <ToolGroupContainer
          key={running ? 'running' : 'done'}
          toolName={groupToolName(part)}
          count={part.indices.length}
          running={running}
          indices={part.indices}
        >
          {children}
        </ToolGroupContainer>
      </div>
    )
  }

  switch (part.type) {
    case 'text':
      return <OrderedTextPart />
    case 'tool-call': {
      const leaf = part as Extract<EnrichedPartState, { type: 'tool-call' }>
      return (
        <OrderedToolCall
          toolUI={leaf.toolUI}
          toolCallId={leaf.toolCallId}
          toolName={leaf.toolName}
          args={leaf.args as Record<string, unknown>}
          result={leaf.result}
          status={leaf.status}
        />
      )
    }
    case 'data': {
      const leaf = part as Extract<EnrichedPartState, { type: 'data' }>
      return leaf.dataRendererUI
    }
    case 'indicator':
      return null
    default:
      return null
  }
}

export function AssistantMessageParts() {
  return (
    <div className="flex flex-col gap-1.5">
      <MessagePrimitive.GroupedParts groupBy={groupAssistantParts} indicator="never">
        {renderGroupedAssistantPart}
      </MessagePrimitive.GroupedParts>
    </div>
  )
}
