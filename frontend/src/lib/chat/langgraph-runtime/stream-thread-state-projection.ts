import {
  AIMessage,
  HumanMessage,
  ToolMessage,
  coerceMessageLikeToMessage,
  isBaseMessage,
  type BaseMessage,
  type BaseMessageLike,
} from '@langchain/core/messages'
import type { LangGraphInterruptLike } from './hitl-interrupts'
import { isTerminalNoticeStatus, type TerminalNoticeStatus } from './terminal-notice'
import type { Message as MoldyMessage } from '@/lib/types'

export interface ThreadRunNotice {
  readonly id: string
  readonly status: TerminalNoticeStatus
  readonly errorMessage?: string
}

export interface ReloadRunCorrelation {
  readonly conversationId: string
  readonly pendingReload: boolean
  readonly acceptedRunId: string | null
}

export interface ServerMessageMetadataSnapshot {
  readonly byId: ReadonlyMap<string, Record<string, unknown>>
  readonly byIndex: readonly (Record<string, unknown> | null)[]
  readonly idByIndex: readonly (string | null)[]
}

export const EMPTY_SERVER_MESSAGE_METADATA: ServerMessageMetadataSnapshot = {
  byId: new Map(),
  byIndex: [],
  idByIndex: [],
}

export function threadInterruptsFromStream(stream: {
  getThread?: () => { readonly interrupts?: readonly LangGraphInterruptLike[] } | undefined
}): readonly LangGraphInterruptLike[] {
  return stream.getThread?.()?.interrupts ?? []
}

export function interruptsFromThreadState(state: unknown): readonly LangGraphInterruptLike[] {
  if (!isRecord(state)) return []
  const interrupts: LangGraphInterruptLike[] = [...interruptRecords(state.interrupts)]
  const values = isRecord(state.values) ? state.values : {}
  interrupts.push(...interruptRecords(values.__interrupt__))
  const tasks = Array.isArray(state.tasks) ? state.tasks : []
  for (const task of tasks) {
    if (!isRecord(task)) continue
    interrupts.push(...interruptRecords(task.interrupts))
  }
  return interrupts
}

export function terminalRunNoticeFromThreadState(state: unknown): ThreadRunNotice | null {
  if (!isRecord(state)) return null
  const metadata = isRecord(state.metadata) ? state.metadata : {}
  const run = isRecord(metadata.latest_run) ? metadata.latest_run : null
  const id = run?.id
  const status = run?.status
  if (typeof id !== 'string' || !isTerminalNoticeStatus(status)) return null
  const rawErrorMessage = run?.error_message
  const errorMessage =
    status === 'failed' && typeof rawErrorMessage === 'string' && rawErrorMessage.trim()
      ? rawErrorMessage
      : undefined
  return errorMessage ? { id, status, errorMessage } : { id, status }
}

export function terminalFailureIsStaleForCurrentAttempt(
  conversationId: string,
  notice: ThreadRunNotice | null,
  correlation: ReloadRunCorrelation,
): boolean {
  if (notice?.status !== 'failed') return false
  if (correlation.conversationId !== conversationId) return false
  if (correlation.pendingReload) return correlation.acceptedRunId !== notice.id
  return correlation.acceptedRunId !== null && correlation.acceptedRunId !== notice.id
}

export function messageMetadataFromThreadState(state: unknown): ServerMessageMetadataSnapshot {
  if (!isRecord(state)) return EMPTY_SERVER_MESSAGE_METADATA
  const values = isRecord(state.values) ? state.values : {}
  const messages = Array.isArray(values.messages) ? values.messages : []
  const metadataById = new Map<string, Record<string, unknown>>()
  const metadataByIndex: (Record<string, unknown> | null)[] = []
  const idByIndex: (string | null)[] = []
  for (const message of messages) {
    if (!isRecord(message)) {
      metadataByIndex.push(null)
      idByIndex.push(null)
      continue
    }
    const messageId = typeof message.id === 'string' && message.id.length > 0 ? message.id : null
    idByIndex.push(messageId)
    const additionalKwargs = isRecord(message.additional_kwargs) ? message.additional_kwargs : {}
    const metadata = isRecord(additionalKwargs.metadata) ? additionalKwargs.metadata : null
    metadataByIndex.push(metadata)
    if (metadata && messageId) metadataById.set(messageId, metadata)
  }
  return { byId: metadataById, byIndex: metadataByIndex, idByIndex }
}

export function messagesFromThreadState(state: unknown): readonly BaseMessage[] | null {
  if (!isRecord(state)) return null
  const values = isRecord(state.values) ? state.values : {}
  const messages = Array.isArray(values.messages) ? values.messages : null
  if (!messages) return null
  const converted: BaseMessage[] = []
  for (const message of messages) {
    if (isCoercibleMessage(message)) converted.push(coerceMessageLikeToMessage(message))
  }
  return converted
}

export function messagesFromServerMessages(
  messages: readonly MoldyMessage[] | undefined,
): BaseMessage[] {
  if (!messages || messages.length === 0) return []
  return messages.map((message) => {
    if (message.role === 'user') {
      return new HumanMessage({ id: message.id, content: message.content })
    }
    if (message.role === 'tool') {
      return new ToolMessage({
        id: message.id,
        content: message.content,
        tool_call_id: message.tool_call_id ?? '',
      })
    }
    return new AIMessage({ id: message.id, content: message.content })
  })
}

function interruptRecords(value: unknown): readonly LangGraphInterruptLike[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is LangGraphInterruptLike => isRecord(item))
}

function isCoercibleMessage(value: unknown): value is BaseMessageLike {
  if (isBaseMessage(value)) return true
  if (!isRecord(value)) return false
  const type = value.type
  const role = value.role
  return (typeof type === 'string' || typeof role === 'string') && 'content' in value
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}
