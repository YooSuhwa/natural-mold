import type { Message } from '@ag-ui/client'
import {
  fromThreadMessageLike,
  type MessageStatus,
  type ThreadAssistantMessage,
  type ThreadAssistantMessagePart,
  type ThreadMessage,
  type ToolCallMessagePart,
} from '@assistant-ui/react'
import { fromAgUiMessages } from '@assistant-ui/react-ag-ui'
import type { SubagentDiscoverySnapshot } from '@langchain/react'
import { resolveDataUI } from '@/lib/chat/data-ui-registry'
import { isBoundAgUiDataCard, matchesAgUiDataCardCall } from './activity-card-owner'
import type { DeepAgentTodo } from '@/lib/chat/langgraph-runtime/deepagents-state'

export interface AgUiHistoryChild {
  readonly subagentRunId: string
  readonly parentToolCallId?: string
  readonly parentRunId?: string
  readonly runId?: string
  readonly physicalRunId?: string
  readonly parentSubagentRunId?: string
  readonly parentMessageId?: string
  readonly name: string
  readonly description?: string
  readonly status: 'running' | 'completed' | 'failed' | 'cancelled' | 'interrupted'
  readonly result?: string
  readonly error?: string
  readonly interruptIds?: readonly string[]
  readonly childTaskId?: string
  readonly legacyInterruptAccess?: true
  readonly todos?: readonly DeepAgentTodo[]
}

export interface AgUiSubagentSnapshot extends SubagentDiscoverySnapshot {
  readonly subagentRunId: string
  readonly parentMessageId: string
  readonly childTaskId?: string
  readonly todos?: readonly DeepAgentTodo[]
  readonly outcome: AgUiHistoryChild['status']
  readonly dataCards?: readonly {
    readonly id: string
    readonly type: string
    readonly props: Record<string, unknown>
  }[]
  readonly transcript: readonly { readonly id: string; readonly text: string }[]
  readonly tools: readonly {
    readonly id: string
    readonly name: string
    readonly result?: string
  }[]
}

interface ChildRecord {
  readonly subagentRunId: string
  readonly message: Message
}

// The official run aggregator limits nested subagents to sixteen levels.
const MAX_CHILD_DEPTH = 16
const COMPLETE = { type: 'complete', reason: 'unknown' } as const

function childStatus(child: AgUiHistoryChild): MessageStatus {
  switch (child.status) {
    case 'running':
      return { type: 'running' }
    case 'completed':
      return COMPLETE
    case 'interrupted':
      return { type: 'requires-action', reason: 'interrupt' }
    case 'cancelled':
      return { type: 'incomplete', reason: 'cancelled' }
    case 'failed':
      return { type: 'incomplete', reason: 'error', error: child.error }
  }
}

function outcome(status: MessageStatus): AgUiHistoryChild['status'] {
  switch (status.type) {
    case 'running':
      return 'running'
    case 'complete':
      return 'completed'
    case 'requires-action':
      return 'interrupted'
    case 'incomplete':
      return status.reason === 'cancelled' ? 'cancelled' : 'failed'
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function agUiMetadata(message: ThreadMessage): Record<string, unknown> {
  const metadata = message.metadata.custom.agui
  return isRecord(metadata) ? metadata : {}
}

function groupByKey<T, K>(values: readonly T[], keyOf: (value: T) => K): Map<K, T[]> {
  const grouped = new Map<K, T[]>()
  for (const value of values) {
    const key = keyOf(value)
    const group = grouped.get(key)
    if (group) group.push(value)
    else grouped.set(key, [value])
  }
  return grouped
}

export function readAgUiChildTodos(value: unknown): readonly DeepAgentTodo[] | undefined {
  if (!Array.isArray(value)) return undefined
  const ids = new Set<string>()
  const todos: DeepAgentTodo[] = []
  for (const row of value as unknown[]) {
    if (
      !isRecord(row) ||
      typeof row.id !== 'string' ||
      !row.id.trim() ||
      ids.has(row.id) ||
      typeof row.content !== 'string' ||
      !row.content.trim() ||
      (row.status !== 'pending' && row.status !== 'in_progress' && row.status !== 'completed')
    )
      return undefined
    ids.add(row.id)
    todos.push({ id: row.id, content: row.content, status: row.status })
  }
  return todos
}

function directChildCalls(message: ThreadAssistantMessage) {
  return groupByKey(
    message.content.filter((part) => part.type === 'tool-call'),
    (call) => call.toolCallId,
  )
}

function publicDataCard(
  part: ThreadAssistantMessagePart,
  calls: ReadonlyMap<string, readonly ToolCallMessagePart[]>,
  restoredIssuerId?: string,
) {
  if (part.type !== 'data' || part.name !== 'agui-activity/moldy.ui_data') return undefined
  const data: unknown = part.data
  if (!isRecord(data) || typeof data.type !== 'string') return undefined
  const resolved = resolveDataUI(data.type, data.props)
  if (!resolved) return undefined
  const card = { type: data.type, props: resolved.props }
  if (!isBoundAgUiDataCard(data)) return card
  const publicId = data.public_tool_call_id
  if (typeof publicId !== 'string') return undefined
  const issuers = calls.get(publicId)
  if (issuers?.length !== 1) return undefined
  // A live child wrapper is named for its run. Only its direct tool part
  // identifies the issuing message. A restored part is checked before folding.
  const issuerId = issuers[0].parentId ?? restoredIssuerId
  const owners = new Set(issuerId ? [issuerId] : [])
  if (!matchesAgUiDataCardCall(data, publicId, owners)) return undefined
  return {
    ...card,
    public_tool_call_id: publicId,
    ...(issuerId && { public_message_id: issuerId }),
  }
}

function publicChildParts(message: ThreadAssistantMessage): ThreadAssistantMessagePart[] {
  const parts: ThreadAssistantMessagePart[] = []
  const calls = directChildCalls(message)
  for (const part of message.content) {
    if (part.type === 'text') parts.push({ type: 'text', text: part.text })
    if (part.type === 'data') {
      const data = publicDataCard(part, calls, message.id)
      if (data) parts.push({ type: 'data', name: part.name, data })
    }
    if (part.type !== 'tool-call') continue
    // Child tool details have a narrower visibility contract than root tool calls.
    parts.push({
      type: 'tool-call',
      toolCallId: part.toolCallId,
      toolName: part.toolName,
      parentId: part.parentId ?? message.id,
      args: {},
      argsText: '{}',
      ...(typeof part.result === 'string' && { result: part.result }),
    })
  }
  return parts
}

/** Rebuild the live SDK's tool-call.messages tree from separately scoped snapshot rows. */
export function nestAgUiHistoryChildren(
  rootMessages: readonly ThreadMessage[],
  childRecords: readonly ChildRecord[],
  children: readonly AgUiHistoryChild[],
): ThreadMessage[] {
  const byId = groupByKey(children, (child) => child.subagentRunId)
  const byParent = groupByKey(children, (child) => child.parentSubagentRunId)
  const byRun = groupByKey(childRecords, (record) => record.subagentRunId)

  function attach(
    messages: readonly ThreadMessage[],
    parentRunId: string | undefined,
    depth: number,
  ): ThreadMessage[] {
    const candidates = byParent.get(parentRunId)
    if (!candidates || depth > MAX_CHILD_DEPTH) return [...messages]
    const calls = messages.flatMap((message) =>
      message.role === 'assistant'
        ? message.content.flatMap((part) => (part.type === 'tool-call' ? [{ message, part }] : []))
        : [],
    )
    const owners = groupByKey(calls, (owner) => owner.part.toolCallId)
    const nested = new Map<ToolCallMessagePart, ThreadMessage[]>()
    for (const child of candidates) {
      if (!child.parentToolCallId || byId.get(child.subagentRunId)?.length !== 1) continue
      const matches = (owners.get(child.parentToolCallId) ?? []).filter(
        ({ message }) =>
          !child.parentMessageId ||
          message.id === child.parentMessageId ||
          agUiMetadata(message).messageId === child.parentMessageId,
      )
      if (matches.length !== 1) continue
      const { part, message: parent } = matches[0]
      const wire = (byRun.get(child.subagentRunId) ?? [])
        .map((record) => record.message)
        .filter(
          (message) =>
            (message.role === 'assistant' ||
              message.role === 'tool' ||
              (message.role === 'activity' && message.activityType === 'moldy.ui_data')) &&
            (!message.subagentRunId || message.subagentRunId === child.subagentRunId),
        )
      // The SDK coalesces repeated tool IDs in one wire record. Check issuance
      // before conversion so a malformed duplicate cannot authorize a card.
      const sourceCalls = groupByKey(
        wire.flatMap((message) => (message.role === 'assistant' ? (message.toolCalls ?? []) : [])),
        (call) => call.id,
      )
      const verified = wire.filter((message) => {
        if (message.role !== 'activity' || !isBoundAgUiDataCard(message.content)) return true
        const publicId = message.content.public_tool_call_id
        return typeof publicId === 'string' && sourceCalls.get(publicId)?.length === 1
      })
      const converted = fromAgUiMessages(verified, {
        showThinking: false,
        joinAdjacentAssistantMessages: false,
      }).flatMap((like) => {
        if (!like.id || like.role !== 'assistant') return []
        const message = fromThreadMessageLike(
          { ...like, createdAt: like.createdAt ?? parent.createdAt },
          like.id,
          COMPLETE,
        )
        return message.role === 'assistant'
          ? [{ ...message, content: publicChildParts(message) }]
          : []
      })
      // Resolve the next generation before folding this child's wire messages into its SDK wrapper.
      const descendants = attach(converted, child.subagentRunId, depth + 1)
      const subagent = fromThreadMessageLike(
        {
          role: 'assistant',
          id: child.subagentRunId,
          createdAt: parent.createdAt,
          status: childStatus(child),
          content: descendants.flatMap((message) =>
            message.role === 'assistant' ? [...message.content] : [],
          ),
          metadata: {
            custom: {
              agui: {
                name: child.name,
                ...(child.description !== undefined && { description: child.description }),
                ...(child.parentMessageId && { parentMessageId: child.parentMessageId }),
                ...(parentRunId && { parentSubagentRunId: parentRunId }),
                ...(child.result !== undefined && { result: child.result }),
                ...(child.todos?.length && { todos: child.todos }),
                ...(child.interruptIds?.length && { interruptIds: child.interruptIds }),
                ...(child.childTaskId && { childTaskId: child.childTaskId }),
              },
            },
          },
        },
        child.subagentRunId,
        COMPLETE,
      )
      nested.set(part, [...(nested.get(part) ?? []), subagent])
    }
    return messages.map((message) => {
      if (message.role !== 'assistant') return message
      return {
        ...message,
        content: message.content.map((part) =>
          part.type === 'tool-call' && nested.has(part)
            ? { ...part, messages: nested.get(part) }
            : part,
        ),
      }
    })
  }
  return attach(rootMessages, undefined, 1)
}

/** Read only the official nested assistant messages with a declared child name and a unique run identity. */
export function indexAgUiSubagents(
  messages: readonly ThreadMessage[],
): ReadonlyMap<string, AgUiSubagentSnapshot> {
  const candidates: AgUiSubagentSnapshot[] = []
  function walk(message: ThreadMessage, ancestors: readonly string[], parentCallId: string | null) {
    if (message.role !== 'assistant' || ancestors.length >= MAX_CHILD_DEPTH) return
    for (const part of message.content) {
      if (part.type !== 'tool-call') continue
      for (const child of part.messages ?? []) {
        if (child.role !== 'assistant') continue
        const meta = agUiMetadata(child)
        if (
          typeof meta.name !== 'string' ||
          !meta.name ||
          !child.id ||
          ancestors.includes(child.id)
        )
          continue
        if (meta.parentSubagentRunId !== undefined && meta.parentSubagentRunId !== ancestors.at(-1))
          continue
        const namespace = [...ancestors, child.id]
        const state = outcome(child.status)
        const todos = readAgUiChildTodos(meta.todos)
        const transcript = child.content.flatMap((entry, i) =>
          entry.type === 'text' ? [{ id: child.id + ':' + i, text: entry.text }] : [],
        )
        const calls = directChildCalls(child)
        const dataCards = child.content.flatMap((entry, i) => {
          const data = publicDataCard(entry, calls)
          return data ? [{ id: child.id + ':' + i, type: data.type, props: data.props }] : []
        })
        const tools = child.content.flatMap((entry) =>
          entry.type === 'tool-call'
            ? [
                {
                  id: entry.toolCallId,
                  name: entry.toolName,
                  ...(typeof entry.result === 'string' && { result: entry.result }),
                },
              ]
            : [],
        )
        candidates.push({
          id: part.toolCallId,
          subagentRunId: child.id,
          name: meta.name,
          parentMessageId: message.id,
          ...(typeof meta.childTaskId === 'string' && { childTaskId: meta.childTaskId }),
          namespace,
          parentId: parentCallId,
          depth: namespace.length,
          outcome: state,
          status:
            state === 'running' || state === 'interrupted'
              ? 'running'
              : state === 'completed'
                ? 'complete'
                : 'error',
          taskInput: typeof meta.description === 'string' ? meta.description : undefined,
          output: typeof meta.result === 'string' ? meta.result : undefined,
          error:
            child.status.type === 'incomplete' && typeof child.status.error === 'string'
              ? child.status.error
              : undefined,
          startedAt: child.createdAt,
          completedAt: null,
          transcript,
          tools,
          ...(dataCards.length > 0 && { dataCards }),
          ...(todos !== undefined && { todos }),
        })
        walk(child, namespace, part.toolCallId)
      }
    }
  }
  for (const message of messages) walk(message, [], null)
  const byRun = groupByKey(candidates, (child) => child.subagentRunId)
  return new Map(
    candidates
      .filter((child) => child.namespace.every((id) => byRun.get(id)?.length === 1))
      .map((child) => [child.subagentRunId, child]),
  )
}
