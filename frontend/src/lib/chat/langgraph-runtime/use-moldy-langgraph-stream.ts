'use client'

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react'
import {
  useExternalMessageConverter,
  useExternalStoreRuntime,
  type AttachmentAdapter,
  type AppendMessage,
  type FeedbackAdapter,
} from '@assistant-ui/react'
import { useChannel, useStream, type Channel } from '@langchain/react'
import {
  AIMessage,
  HumanMessage,
  ToolMessage,
  coerceMessageLikeToMessage,
  isBaseMessage,
  type BaseMessage,
  type BaseMessageLike,
} from '@langchain/core/messages'
import { useSetAtom } from 'jotai'
import { useTranslations } from 'next-intl'
import { flushSync } from 'react-dom'
import { reduceProtocolActivity } from './activity-protocol'
import { useLangGraphArtifactEffects } from './artifact-events'
import { MOLDY_BRANCH_SWITCHED_EVENT, isMoldyBranchSwitchedEvent } from './branch-switch-events'
import { selectDeepAgentsState } from './deepagents-state'
import {
  activeInterruptPayloads,
  appendInterruptToolCallMessages,
  appendResolvedInterruptToolCallMessages,
  resolvedInterruptToolCallsFromDecisions,
  standardPayloadsFromInterrupts,
  type LangGraphInterruptLike,
  type ResolvedInterruptToolCall,
} from './hitl-interrupts'
import { useLangGraphMemoryEffects } from './memory-events'
import {
  dedupeLangChainMessagesById,
  sourceMessageIdFromThreadMessageId,
  stableString,
  useStableConvertedMessages,
} from './message-list'
import { createMoldyAgentTransport, type MoldyAgentServerAdapter } from './moldy-agent-transport'
import { refreshThreadLifecycleStream } from './lifecycle-subscription'
import { loadServerThreadState, type ThreadStateResponse } from './thread-state-checkpoints'
import {
  useCheckpointForkHandlers,
  type PendingCheckpointEditSubmit,
} from './use-checkpoint-fork-handlers'
import { useLangGraphUsageEffects } from './usage-events'
import { convertMoldyLangChainMessage } from './langchain-message-conversion'
import type { RunActivity } from './activity-model'
import { createHiTLDecisionCoordinator, type HiTLDecisionCoordinator } from '../standard-interrupt'
import { conversationRunsApi } from '@/lib/api/conversation-runs'
import {
  chatCancelInFlightAtom,
  pendingEditBranchPickerSuppressionAtom,
} from '@/lib/stores/chat-store'
import type { Decision, Message as MoldyMessage } from '@/lib/types'

type ConvertedMessage = ReturnType<typeof useExternalMessageConverter>[number]
type VisibleMessageWithId = {
  readonly id: string
  readonly role?: string
  readonly sourceId?: string
}

interface MoldyGraphState {
  messages: BaseMessage[]
  todos?: unknown
  files?: unknown
  async_tasks?: unknown
}

interface UseMoldyLangGraphStreamOptions {
  agentId: string
  conversationId: string
  feedbackAdapter?: FeedbackAdapter
  attachmentAdapter?: AttachmentAdapter
  onBeforeSubmit?: () => void
  onRunStartAccepted?: () => void
  serverMessages?: readonly MoldyMessage[]
}

interface ThreadRunNotice {
  readonly id: string
  readonly status: 'canceled' | 'canceling' | 'stale'
}

interface ThreadRunNoticeSnapshot {
  readonly conversationId: string
  readonly notice: ThreadRunNotice | null
}

interface ServerMessageMetadataSnapshot {
  readonly byId: ReadonlyMap<string, Record<string, unknown>>
  readonly byIndex: readonly (Record<string, unknown> | null)[]
  readonly idByIndex: readonly (string | null)[]
}

interface ServerMessageMetadataState {
  readonly conversationId: string
  readonly snapshot: ServerMessageMetadataSnapshot
}

interface ServerStateMessagesSnapshot {
  readonly conversationId: string
  readonly messages: readonly BaseMessage[]
}

interface ServerInterruptsSnapshot {
  readonly conversationId: string
  readonly interrupts: readonly LangGraphInterruptLike[]
}

interface PendingEditRenderState extends PendingCheckpointEditSubmit {
  readonly conversationId: string
  readonly staleTailFingerprints: readonly string[]
  readonly staleTailContentFingerprints: readonly string[]
  readonly staleConvertedTailContentFingerprints: readonly string[]
  readonly requiresLatestBranchMetadata: boolean
  readonly pendingBranchTotal: number | null
}

interface PendingReloadRenderState {
  readonly conversationId: string
  readonly parentId: string | null
  readonly targetId: string | null
  readonly targetIndex: number | null
  readonly promptMessageKey: string | null
  readonly staleMessageKey: string
  readonly requiredAssistantBranch: PendingReloadRequiredAssistantBranch
  readonly requiredUserBranch: PendingReloadRequiredUserBranch | null
}

interface PendingNewSubmitState {
  readonly conversationId: string
  readonly content: string
  readonly baseMessageCount: number
  readonly message: HumanMessage
}

interface PendingReloadRequiredAssistantBranch {
  readonly index: number
  readonly branchTotal: number
}

interface PendingReloadRequiredUserBranch {
  readonly id: string | null
  readonly index: number
  readonly branchTotal: number
}

interface ResolvedInterruptsSnapshot {
  readonly conversationId: string
  readonly items: readonly ResolvedInterruptToolCall[]
}

interface ThreadStateHydrationOptions {
  readonly replaceMessages?: boolean
}

const EMPTY_SERVER_MESSAGE_METADATA: ServerMessageMetadataSnapshot = {
  byId: new Map(),
  byIndex: [],
  idByIndex: [],
}
const EMPTY_RESOLVED_INTERRUPTS: readonly ResolvedInterruptToolCall[] = []
const STICKY_MESSAGE_CACHE_LIMIT = 50
const PENDING_EDIT_HYDRATION_RETRY_MS = 150
const POST_RUN_HYDRATION_RETRY_MS = 150
const POST_RUN_HYDRATION_TIMEOUT_MS = 10_000
const stickyMessagesByConversation = new Map<string, readonly BaseMessage[]>()
const stickyConvertedMessagesByConversation = new Map<string, readonly ConvertedMessage[]>()
const pendingNewSubmitsByConversation = new Map<string, PendingNewSubmitState>()
const pendingNewSubmitListeners = new Set<() => void>()
const BRANCH_PICKER_METADATA_KEYS = [
  'branches',
  'siblingCheckpointIds',
  'activeBranchId',
  'branchCheckpointId',
  'branchIndex',
  'branchTotal',
  'checkpoint_id',
  'moldyBranchPickerDisplayOnly',
] as const
const CLEARED_BRANCH_PICKER_METADATA = {
  branches: [],
  siblingCheckpointIds: [],
  activeBranchId: null,
  branchCheckpointId: null,
  branchIndex: null,
  branchTotal: null,
  checkpoint_id: null,
  moldyBranchPickerDisplayOnly: null,
  moldySuppressBranchPicker: true,
} as const

const ACTIVITY_CHANNELS = [
  'messages',
  'tools',
  'values',
  'updates',
  'lifecycle',
  'tasks',
  'checkpoints',
  'custom',
] as const satisfies readonly Channel[]

const PERSISTED_CONVERSATION_ID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function threadInterruptsFromStream(stream: {
  getThread?: () => { readonly interrupts?: readonly LangGraphInterruptLike[] } | undefined
}): readonly LangGraphInterruptLike[] {
  return stream.getThread?.()?.interrupts ?? []
}

function interruptRecords(value: unknown): readonly LangGraphInterruptLike[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is LangGraphInterruptLike => isRecord(item))
}

function interruptsFromThreadState(state: unknown): readonly LangGraphInterruptLike[] {
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

function terminalRunNoticeFromThreadState(state: unknown): ThreadRunNotice | null {
  if (!isRecord(state)) return null
  const metadata = isRecord(state.metadata) ? state.metadata : {}
  const run = isRecord(metadata.latest_run) ? metadata.latest_run : null
  const id = run?.id
  const status = run?.status
  if (typeof id !== 'string') return null
  if (status !== 'canceled' && status !== 'canceling' && status !== 'stale') return null
  return { id, status }
}

function messageMetadataFromThreadState(state: unknown): ServerMessageMetadataSnapshot {
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

function messagesFromThreadState(state: unknown): readonly BaseMessage[] | null {
  if (!isRecord(state)) return null
  const values = isRecord(state.values) ? state.values : {}
  const messages = Array.isArray(values.messages) ? values.messages : null
  if (!messages) return null

  const converted: BaseMessage[] = []
  for (const message of messages) {
    if (!isCoercibleMessage(message)) continue
    converted.push(coerceMessageLikeToMessage(message))
  }
  return converted
}

function messagesFromServerMessages(messages: readonly MoldyMessage[] | undefined): BaseMessage[] {
  if (!messages || messages.length === 0) return []
  return messages.map((message) => {
    if (message.role === 'user') {
      return new HumanMessage({
        id: message.id,
        content: message.content,
      })
    }
    if (message.role === 'tool') {
      return new ToolMessage({
        id: message.id,
        content: message.content,
        tool_call_id: message.tool_call_id ?? '',
      })
    }
    return new AIMessage({
      id: message.id,
      content: message.content,
    })
  })
}

export function primeStickyConversationMessagesFromThreadState(
  conversationId: string,
  state: unknown,
): boolean {
  const messages = messagesFromThreadState(state)
  if (!messages || messages.length === 0) return false
  if (!hasReadyAssistantMessage(messages)) return false
  cacheStickyMessages(conversationId, messages)
  return true
}

function lastAssistantMessage(messages: readonly BaseMessage[]): BaseMessage | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (isAssistantMessage(message)) return message
  }
  return null
}

function hasReadyAssistantMessage(messages: readonly BaseMessage[]): boolean {
  const lastAssistant = lastAssistantMessage(messages)
  return Boolean(lastAssistant && !isEmptyAssistantMessage(lastAssistant))
}

function hasReadyAssistantAfterLastHumanMessage(messages: readonly BaseMessage[]): boolean {
  const lastHumanIndex = lastHumanMessageIndex(messages)
  if (lastHumanIndex < 0) return hasReadyAssistantMessage(messages)
  return messages
    .slice(lastHumanIndex + 1)
    .some((message) => isAssistantMessage(message) && !isEmptyAssistantMessage(message))
}

function isCoercibleMessage(value: unknown): value is BaseMessageLike {
  if (isBaseMessage(value)) return true
  if (!isRecord(value)) return false
  const type = value.type
  const role = value.role
  return (typeof type === 'string' || typeof role === 'string') && 'content' in value
}

function mergeServerMessageMetadata(
  messages: readonly BaseMessage[],
  metadataSnapshot: ServerMessageMetadataSnapshot,
): BaseMessage[] {
  if (
    metadataSnapshot.byId.size === 0 &&
    metadataSnapshot.byIndex.length === 0 &&
    metadataSnapshot.idByIndex.length === 0
  ) {
    return [...messages]
  }
  return messages.map((message, index) => {
    const messageId = message.id
    const metadata =
      (messageId ? metadataSnapshot.byId.get(messageId) : undefined) ??
      metadataSnapshot.byIndex[index]
    const replacementId = replacementMessageId(message, metadataSnapshot.idByIndex[index])
    if (!metadata && !replacementId) return message
    return withAdditionalMessageMetadata(message, metadata ?? {}, replacementId)
  })
}

function applyPendingEditBranchMetadata(
  messages: readonly BaseMessage[],
  pendingEdit: PendingEditRenderState | null,
): readonly BaseMessage[] {
  if (!pendingEdit) return messages
  const targetIndex = pendingEditTargetIndex(messages, pendingEdit)
  if (targetIndex < 0) return messages
  const targetMessage = messages[targetIndex]
  if (!targetMessage) return messages
  if (!messageContentEqualsText(targetMessage, pendingEdit.content)) return messages
  const replacement = cloneMessageWithPendingEditBranchMetadata(targetMessage, pendingEdit)
  if (!replacement || replacement === targetMessage) return messages
  return [...messages.slice(0, targetIndex), replacement, ...messages.slice(targetIndex + 1)]
}

function applyPendingReloadBranchMetadata(
  messages: readonly BaseMessage[],
  pendingReload: PendingReloadRenderState | null,
): readonly BaseMessage[] {
  if (!pendingReload) return messages
  const targetIndex = pendingReloadBranchMetadataTargetIndex(messages, pendingReload)
  if (targetIndex < 0) return messages
  const targetMessage = messages[targetIndex]
  if (!isAssistantMessage(targetMessage)) return messages
  const replacement = cloneMessageWithPendingReloadBranchMetadata(targetMessage, pendingReload)
  if (replacement === targetMessage) return messages
  return [...messages.slice(0, targetIndex), replacement, ...messages.slice(targetIndex + 1)]
}

function cloneMessageWithPendingReloadBranchMetadata(
  message: BaseMessage,
  pendingReload: PendingReloadRenderState,
): BaseMessage {
  const branchTotal = pendingReload.requiredAssistantBranch.branchTotal + 1
  const branchIndex = branchTotal - 1
  const additionalKwargs = isRecord(message.additional_kwargs) ? message.additional_kwargs : {}
  const metadata = isRecord(additionalKwargs.metadata) ? additionalKwargs.metadata : {}
  const clone = Object.create(Object.getPrototypeOf(message)) as BaseMessage
  Object.assign(clone, message, {
    additional_kwargs: {
      ...additionalKwargs,
      metadata: {
        ...withoutBranchPickerCustomMetadata(metadata),
        branches: Array.from({ length: branchTotal }, (_, index) => `pending-reload-${index}`),
        siblingCheckpointIds: Array.from(
          { length: branchTotal },
          (_, index) => `pending-reload-${index}`,
        ),
        activeBranchId: `pending-reload-${branchIndex}`,
        branchCheckpointId: `pending-reload-${branchIndex}`,
        branchIndex,
        branchTotal,
        checkpoint_id: `pending-reload-${branchIndex}`,
        moldyBranchPickerDisplayOnly: true,
      },
    },
  })
  return clone
}

function cloneMessageWithPendingEditBranchMetadata(
  message: BaseMessage,
  pendingEdit: PendingEditRenderState,
): BaseMessage | undefined {
  const additionalKwargs = isRecord(message.additional_kwargs) ? message.additional_kwargs : {}
  const metadata = isRecord(additionalKwargs.metadata) ? additionalKwargs.metadata : {}
  const nextMetadata =
    pendingEdit.pendingBranchTotal !== null
      ? pendingBranchPickerCustomMetadata(metadata, pendingEdit.pendingBranchTotal, 'pending-edit')
      : clearedBranchPickerCustomMetadata(metadata)
  const nextAdditionalKwargs = {
    ...additionalKwargs,
    metadata: nextMetadata,
  }
  if (stableString(nextAdditionalKwargs) === stableString(message.additional_kwargs)) return message
  const clone = Object.create(Object.getPrototypeOf(message)) as BaseMessage
  Object.assign(clone, message, {
    additional_kwargs: nextAdditionalKwargs,
  })
  return clone
}

function replacementMessageId(
  message: BaseMessage,
  candidate: string | null | undefined,
): string | null {
  if (!candidate) return null
  const current = message.id
  if (typeof current !== 'string' || current.length === 0) return candidate
  if (current.startsWith('opt-') || current.startsWith('stream-')) return candidate
  return null
}

function withAdditionalMessageMetadata(
  message: BaseMessage,
  metadata: Record<string, unknown>,
  replacementId: string | null = null,
): BaseMessage {
  const additionalKwargs = isRecord(message.additional_kwargs) ? message.additional_kwargs : {}
  const existingMetadata = isRecord(additionalKwargs.metadata) ? additionalKwargs.metadata : {}
  const clone = Object.create(Object.getPrototypeOf(message)) as BaseMessage
  Object.assign(clone, message, {
    ...(replacementId ? { id: replacementId } : {}),
    additional_kwargs: {
      ...additionalKwargs,
      metadata: {
        ...existingMetadata,
        ...metadata,
      },
    },
  })
  return clone
}

function appendTerminalRunNotice(
  messages: readonly BaseMessage[],
  notice: ThreadRunNotice | null,
  text: string,
): BaseMessage[] {
  if (!notice) return [...messages]
  const id = `moldy-${notice.status}-${notice.id}`
  if (messages.some((message) => message.id === id)) return [...messages]
  return [
    ...messages,
    new AIMessage({
      id,
      content: text,
      additional_kwargs: {
        metadata: {
          moldy_terminal_notice: notice.status,
        },
      },
    }),
  ]
}

function cacheStickyMessages(conversationId: string, messages: readonly BaseMessage[]): void {
  if (messages.length === 0) return
  stickyMessagesByConversation.set(conversationId, snapshotBaseMessages(messages))
  if (stickyMessagesByConversation.size <= STICKY_MESSAGE_CACHE_LIMIT) return
  const oldestKey = stickyMessagesByConversation.keys().next().value
  if (typeof oldestKey === 'string') stickyMessagesByConversation.delete(oldestKey)
}

function cacheStickyConvertedMessages(
  conversationId: string,
  messages: readonly ConvertedMessage[],
): void {
  if (messages.length === 0) return
  stickyConvertedMessagesByConversation.set(conversationId, messages)
  if (stickyConvertedMessagesByConversation.size <= STICKY_MESSAGE_CACHE_LIMIT) return
  const oldestKey = stickyConvertedMessagesByConversation.keys().next().value
  if (typeof oldestKey === 'string') stickyConvertedMessagesByConversation.delete(oldestKey)
}

function cachePendingNewSubmit(pending: PendingNewSubmitState): void {
  pendingNewSubmitsByConversation.set(pending.conversationId, pending)
  if (pendingNewSubmitsByConversation.size > STICKY_MESSAGE_CACHE_LIMIT) {
    const oldestKey = pendingNewSubmitsByConversation.keys().next().value
    if (typeof oldestKey === 'string') pendingNewSubmitsByConversation.delete(oldestKey)
  }
  emitPendingNewSubmitChange()
}

function clearPendingNewSubmit(conversationId: string, content: string): void {
  const current = pendingNewSubmitsByConversation.get(conversationId)
  if (current?.content !== content) return
  pendingNewSubmitsByConversation.delete(conversationId)
  emitPendingNewSubmitChange()
}

function clearPendingNewSubmitForConversation(conversationId: string): void {
  if (!pendingNewSubmitsByConversation.delete(conversationId)) return
  emitPendingNewSubmitChange()
}

function emitPendingNewSubmitChange(): void {
  for (const listener of pendingNewSubmitListeners) listener()
}

function subscribePendingNewSubmitStore(listener: () => void): () => void {
  pendingNewSubmitListeners.add(listener)
  return () => pendingNewSubmitListeners.delete(listener)
}

function getEmptyPendingNewSubmitSnapshot(): PendingNewSubmitState | null {
  return null
}

function clearStickyConversationMessages(conversationId: string): void {
  stickyMessagesByConversation.delete(conversationId)
  stickyConvertedMessagesByConversation.delete(conversationId)
}

function snapshotBaseMessages(messages: readonly BaseMessage[]): readonly BaseMessage[] {
  return messages.map(cloneBaseMessageSnapshot)
}

type SnapshotCloneableMessage = BaseMessage & {
  readonly additional_kwargs?: unknown
  readonly invalid_tool_calls?: unknown
  readonly response_metadata?: unknown
  readonly status?: unknown
  readonly tool_call_id?: unknown
  readonly tool_calls?: unknown
  readonly usage_metadata?: unknown
}

function cloneBaseMessageSnapshot(message: BaseMessage): BaseMessage {
  const source = message as SnapshotCloneableMessage
  const clone = Object.create(Object.getPrototypeOf(message)) as BaseMessage
  Object.assign(clone, message, {
    content: snapshotValue(message.content),
    additional_kwargs: snapshotValue(source.additional_kwargs),
    response_metadata: snapshotValue(source.response_metadata),
    status: snapshotValue(source.status),
    tool_calls: snapshotValue(source.tool_calls),
    invalid_tool_calls: snapshotValue(source.invalid_tool_calls),
    tool_call_id: snapshotValue(source.tool_call_id),
    usage_metadata: snapshotValue(source.usage_metadata),
  })
  return clone
}

function snapshotValue<T>(value: T): T {
  if (value === null || value === undefined) return value
  if (typeof globalThis.structuredClone === 'function') {
    try {
      return globalThis.structuredClone(value)
    } catch {
      // Fall through for class instances or other non-cloneable values.
    }
  }
  if (Array.isArray(value)) {
    return value.map((item) => snapshotValue(item)) as T
  }
  if (isRecord(value)) {
    const cloned: Record<string, unknown> = {}
    for (const [key, nestedValue] of Object.entries(value)) {
      cloned[key] = snapshotValue(nestedValue)
    }
    return cloned as T
  }
  return value
}

function activePendingEditRenderState(
  conversationId: string,
  pendingEdit: PendingEditRenderState | null,
): PendingEditRenderState | null {
  return pendingEdit?.conversationId === conversationId ? pendingEdit : null
}

function activePendingReloadRenderState(
  conversationId: string,
  pendingReload: PendingReloadRenderState | null,
): PendingReloadRenderState | null {
  return pendingReload?.conversationId === conversationId ? pendingReload : null
}

function applyPendingEditRenderState(
  messages: readonly BaseMessage[],
  pendingEdit: PendingEditRenderState | null,
): readonly BaseMessage[] {
  if (!pendingEdit) return messages
  const targetIndex = pendingEditTargetIndex(messages, pendingEdit)
  if (targetIndex < 0) return messages

  const optimisticIndex = pendingEditOptimisticIndex(messages, pendingEdit, targetIndex)
  const replacementMessage = editedHumanMessage(messages[targetIndex], pendingEdit)
  if (optimisticIndex > targetIndex) {
    return [
      ...messages.slice(0, targetIndex),
      replacementMessage,
      ...messages.slice(optimisticIndex + 1),
    ]
  }
  return [
    ...messages.slice(0, targetIndex),
    replacementMessage,
    ...pendingEditTailAfterStaleMessages(messages, pendingEdit, targetIndex),
  ]
}

function applyPendingReloadRenderState(
  messages: readonly BaseMessage[],
  pendingReload: PendingReloadRenderState | null,
): readonly BaseMessage[] {
  if (!pendingReload) return messages
  const targetIndex = pendingReloadTargetIndex(messages, pendingReload)
  if (targetIndex < 0) return messages
  const targetMessage = messages[targetIndex]
  if (!targetMessage || messageRenderKey(targetMessage) !== pendingReload.staleMessageKey) {
    return messages
  }
  return [...messages.slice(0, targetIndex), ...messages.slice(targetIndex + 1)]
}

function pendingEditTargetIndex(
  messages: readonly BaseMessage[],
  pendingEdit: Pick<
    PendingCheckpointEditSubmit,
    'parentId' | 'sourceId' | 'targetId' | 'targetIndex'
  >,
): number {
  for (const candidateId of [pendingEdit.sourceId, pendingEdit.parentId, pendingEdit.targetId]) {
    if (!candidateId) continue
    const index = messages.findIndex((message) => message.id === candidateId)
    if (index >= 0) return index
  }
  if (pendingEdit.targetIndex != null) {
    const indexedMessage = messages[pendingEdit.targetIndex]
    if (indexedMessage && isHumanMessage(indexedMessage)) return pendingEdit.targetIndex
  }
  return -1
}

function pendingEditVisibleTarget(
  detail: Pick<PendingEditRenderState, 'parentId' | 'sourceId'>,
  visibleMessages: readonly VisibleMessageWithId[],
): { readonly id: string | null; readonly index: number | null } {
  for (const candidateId of [detail.sourceId, detail.parentId]) {
    if (!candidateId) continue
    const index = visibleMessages.findIndex((message) => message.id === candidateId)
    if (index >= 0) return { id: candidateId, index }
  }
  return { id: null, index: null }
}

function pendingReloadRenderFromParentId(
  conversationId: string,
  parentId: string | null,
  visibleMessages: readonly VisibleMessageWithId[],
  currentMessages: readonly BaseMessage[],
): PendingReloadRenderState | null {
  const target = pendingReloadVisibleTarget(parentId, visibleMessages, currentMessages)
  const targetIndex = pendingReloadTargetIndex(currentMessages, target)
  const targetMessage = targetIndex >= 0 ? currentMessages[targetIndex] : undefined
  if (!targetMessage || !isAssistantMessage(targetMessage)) return null
  return {
    conversationId,
    parentId,
    targetId: target.targetId,
    targetIndex,
    promptMessageKey: reloadPromptMessageKey(currentMessages, targetIndex),
    staleMessageKey: messageRenderKey(targetMessage),
    requiredAssistantBranch: requiredAssistantBranchForReload(targetMessage, targetIndex),
    requiredUserBranch: requiredUserBranchForReload(currentMessages, targetIndex),
  }
}

function requiredAssistantBranchForReload(
  message: BaseMessage,
  targetIndex: number,
): PendingReloadRequiredAssistantBranch {
  const metadata = branchMetadataFromMessage(message)
  const branchTotal = metadata?.branchTotal
  return {
    index: targetIndex,
    branchTotal: typeof branchTotal === 'number' && branchTotal >= 2 ? branchTotal : 1,
  }
}

function requiredUserBranchForReload(
  messages: readonly BaseMessage[],
  targetIndex: number,
): PendingReloadRequiredUserBranch | null {
  for (let index = targetIndex - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (!isHumanMessage(message)) continue
    const metadata = branchMetadataFromMessage(message)
    const branchTotal = metadata?.branchTotal
    if (typeof branchTotal !== 'number' || branchTotal < 2) return null
    return {
      id: typeof message.id === 'string' && message.id.length > 0 ? message.id : null,
      index,
      branchTotal,
    }
  }
  return null
}

function pendingReloadVisibleTarget(
  parentId: string | null,
  visibleMessages: readonly VisibleMessageWithId[],
  currentMessages: readonly BaseMessage[],
): Pick<PendingReloadRenderState, 'parentId' | 'targetId' | 'targetIndex'> {
  if (!parentId) {
    const lastAssistantIndex = lastAssistantMessageIndex(currentMessages)
    return {
      parentId,
      targetId: currentMessages[lastAssistantIndex]?.id ?? null,
      targetIndex: lastAssistantIndex >= 0 ? lastAssistantIndex : null,
    }
  }

  const directMessageIndex = currentMessages.findIndex((message) => message.id === parentId)
  if (directMessageIndex >= 0 && isAssistantMessage(currentMessages[directMessageIndex])) {
    return { parentId, targetId: parentId, targetIndex: directMessageIndex }
  }
  const nextMessage = currentMessages[directMessageIndex + 1]
  if (directMessageIndex >= 0 && nextMessage && isAssistantMessage(nextMessage)) {
    return { parentId, targetId: nextMessage.id ?? null, targetIndex: directMessageIndex + 1 }
  }

  const visibleIndex = visibleMessages.findIndex((message) => message.id === parentId)
  const visibleMessage = visibleIndex >= 0 ? visibleMessages[visibleIndex] : undefined
  if (isVisibleAssistantMessage(visibleMessage)) {
    return { parentId, targetId: parentId, targetIndex: visibleIndex }
  }
  const nextVisibleMessage = visibleMessages[visibleIndex + 1]
  if (visibleIndex >= 0 && isVisibleAssistantMessage(nextVisibleMessage)) {
    return { parentId, targetId: nextVisibleMessage.id, targetIndex: visibleIndex + 1 }
  }

  return { parentId, targetId: null, targetIndex: null }
}

function pendingReloadTargetIndex(
  messages: readonly BaseMessage[],
  pendingReload: Pick<PendingReloadRenderState, 'targetId' | 'targetIndex'>,
): number {
  if (pendingReload.targetId) {
    const index = messages.findIndex((message) => message.id === pendingReload.targetId)
    if (index >= 0) return index
  }
  if (pendingReload.targetIndex != null) {
    const indexedMessage = messages[pendingReload.targetIndex]
    if (indexedMessage && isAssistantMessage(indexedMessage)) return pendingReload.targetIndex
  }
  return -1
}

function pendingReloadBranchMetadataTargetIndex(
  messages: readonly BaseMessage[],
  pendingReload: PendingReloadRenderState,
): number {
  const targetIndex = pendingReloadTargetIndex(messages, pendingReload)
  if (targetIndex >= 0) return targetIndex
  return pendingReloadReplacementAssistantIndex(messages, pendingReload)
}

function pendingReloadReplacementAssistantIndex(
  messages: readonly BaseMessage[],
  pendingReload: PendingReloadRenderState,
): number {
  const assistantIndex = lastAssistantMessageIndex(messages)
  if (assistantIndex < 0) return -1
  const lastHumanIndex = lastHumanMessageIndex(messages)
  if (lastHumanIndex > assistantIndex) return -1
  if (messages.length === 1) return assistantIndex
  if (!pendingReload.promptMessageKey) return -1
  const promptIndex = messages.findIndex(
    (message, index) =>
      index < assistantIndex &&
      isHumanMessage(message) &&
      reloadMessageKey(message) === pendingReload.promptMessageKey,
  )
  return promptIndex >= 0 ? assistantIndex : -1
}

function pendingEditRenderFromAppendMessage(
  conversationId: string,
  message: AppendMessage,
  visibleMessages: readonly VisibleMessageWithId[],
  currentMessages: readonly BaseMessage[],
  convertedMessages: readonly ConvertedMessage[] = [],
): PendingEditRenderState | null {
  const content = appendMessageText(message).trim()
  if (!content) return null
  const target = pendingEditVisibleTarget(message, visibleMessages)
  return pendingEditRenderFromSubmit(
    conversationId,
    {
      content,
      parentId: message.parentId ?? null,
      sourceId: message.sourceId ?? null,
      targetId: target.id,
      targetIndex: target.index,
    },
    currentMessages,
    convertedMessages,
  )
}

function pendingEditRenderFromSubmit(
  conversationId: string,
  edit: PendingCheckpointEditSubmit,
  currentMessages: readonly BaseMessage[],
  convertedMessages: readonly ConvertedMessage[] = [],
): PendingEditRenderState {
  return {
    conversationId,
    ...edit,
    staleTailFingerprints: staleTailFingerprintsForEdit(currentMessages, edit),
    staleTailContentFingerprints: staleTailContentFingerprintsForEdit(currentMessages, edit),
    staleConvertedTailContentFingerprints: staleConvertedTailContentFingerprintsForEdit(
      convertedMessages,
      edit,
    ),
    requiresLatestBranchMetadata: editRequiresLatestBranchMetadata(currentMessages, edit),
    pendingBranchTotal: pendingEditBranchTotal(currentMessages, edit),
  }
}

function pendingEditBranchTotal(
  messages: readonly BaseMessage[],
  edit: PendingCheckpointEditSubmit,
): number | null {
  const targetIndex = pendingEditTargetIndex(messages, edit)
  if (targetIndex < 0) return null
  const targetMessage = messages[targetIndex]
  if (!isHumanMessage(targetMessage)) return null
  const metadata = branchMetadataFromMessage(targetMessage)
  const branchTotal = metadata?.branchTotal
  return typeof branchTotal === 'number' && branchTotal >= 2 ? branchTotal + 1 : 2
}

function editRequiresLatestBranchMetadata(
  messages: readonly BaseMessage[],
  edit: PendingCheckpointEditSubmit,
): boolean {
  const targetIndex = pendingEditTargetIndex(messages, edit)
  const targetMessage = targetIndex >= 0 ? messages[targetIndex] : undefined
  const metadata = branchMetadataFromMessage(targetMessage)
  const branchTotal = metadata?.branchTotal
  return typeof branchTotal === 'number' && branchTotal >= 2
}

function staleTailFingerprintsForEdit(
  messages: readonly BaseMessage[],
  edit: PendingCheckpointEditSubmit,
): readonly string[] {
  const targetIndex = pendingEditTargetIndex(messages, edit)
  if (targetIndex < 0) return []
  return messages.slice(targetIndex + 1).map(messageContentFingerprint)
}

function staleTailContentFingerprintsForEdit(
  messages: readonly BaseMessage[],
  edit: PendingCheckpointEditSubmit,
): readonly string[] {
  const targetIndex = pendingEditTargetIndex(messages, edit)
  if (targetIndex < 0) return []
  return messages.slice(targetIndex + 1).map(messageContentRoleFingerprint)
}

function staleConvertedTailContentFingerprintsForEdit(
  messages: readonly ConvertedMessage[],
  edit: PendingCheckpointEditSubmit,
): readonly string[] {
  const targetIndex = pendingEditConvertedTargetIndex(messages, edit)
  if (targetIndex < 0) return []
  return messages.slice(targetIndex + 1).map(convertedMessageContentRoleFingerprint)
}

function pendingEditTailAfterStaleMessages(
  messages: readonly BaseMessage[],
  pendingEdit: PendingEditRenderState,
  targetIndex: number,
): readonly BaseMessage[] {
  const tail = messages.slice(targetIndex + 1)
  if (tail.length === 0) return []
  const stalePrefixLength = staleTailPrefixLength(tail, pendingEdit.staleTailFingerprints)
  if (stalePrefixLength > 0) return tail.slice(stalePrefixLength)
  return tail
}

function staleTailPrefixLength(
  tail: readonly BaseMessage[],
  staleTailFingerprints: readonly string[],
): number {
  const limit = Math.min(tail.length, staleTailFingerprints.length)
  let index = 0
  while (index < limit && messageContentFingerprint(tail[index]) === staleTailFingerprints[index]) {
    index += 1
  }
  return index
}

function pendingEditOptimisticIndex(
  messages: readonly BaseMessage[],
  pendingEdit: PendingEditRenderState,
  targetIndex: number,
): number {
  for (let index = messages.length - 1; index > targetIndex; index -= 1) {
    const message = messages[index]
    if (
      message &&
      isHumanMessage(message) &&
      (textContentFromMessageContent(message.content) === '' ||
        messageContentEqualsText(message, pendingEdit.content))
    ) {
      return index
    }
  }
  return -1
}

function editedHumanMessage(
  originalMessage: BaseMessage | undefined,
  pendingEdit: PendingEditRenderState,
): HumanMessage {
  const id = typeof originalMessage?.id === 'string' ? originalMessage.id : pendingEdit.sourceId
  const additionalKwargs = pendingEditAdditionalKwargs(originalMessage, pendingEdit)
  return new HumanMessage({
    content: pendingEdit.content,
    ...(id ? { id } : {}),
    ...(originalMessage?.name ? { name: originalMessage.name } : {}),
    additional_kwargs: additionalKwargs,
    response_metadata: originalMessage?.response_metadata ?? {},
  })
}

function pendingEditAdditionalKwargs(
  originalMessage: BaseMessage | undefined,
  pendingEdit: PendingEditRenderState,
): Record<string, unknown> {
  const additionalKwargs = isRecord(originalMessage?.additional_kwargs)
    ? originalMessage.additional_kwargs
    : {}
  const metadata = isRecord(additionalKwargs.metadata) ? additionalKwargs.metadata : {}
  const nextMetadata =
    pendingEdit.pendingBranchTotal !== null
      ? pendingBranchPickerCustomMetadata(metadata, pendingEdit.pendingBranchTotal, 'pending-edit')
      : clearedBranchPickerCustomMetadata(metadata)
  return {
    ...additionalKwargs,
    metadata: nextMetadata,
  }
}

function withoutBranchPickerCustomMetadata(value: unknown): Record<string, unknown> {
  const custom = isRecord(value) ? value : {}
  const remaining = { ...custom }
  for (const key of BRANCH_PICKER_METADATA_KEYS) {
    delete remaining[key]
  }
  return remaining
}

function clearedBranchPickerCustomMetadata(value: unknown): Record<string, unknown> {
  return {
    ...withoutBranchPickerCustomMetadata(value),
    ...CLEARED_BRANCH_PICKER_METADATA,
  }
}

function pendingBranchPickerCustomMetadata(
  value: unknown,
  branchTotal: number,
  idPrefix: string,
): Record<string, unknown> {
  const branchIndex = branchTotal - 1
  const branchIds = Array.from({ length: branchTotal }, (_, index) => `${idPrefix}-${index}`)
  return {
    ...withoutBranchPickerCustomMetadata(value),
    branches: branchIds,
    siblingCheckpointIds: branchIds,
    activeBranchId: branchIds[branchIndex] ?? null,
    branchCheckpointId: branchIds[branchIndex] ?? null,
    branchIndex,
    branchTotal,
    checkpoint_id: branchIds[branchIndex] ?? null,
    moldyBranchPickerDisplayOnly: true,
  }
}

function isHumanMessage(message: BaseMessage): boolean {
  return typeof message._getType === 'function' && message._getType() === 'human'
}

function isAssistantMessage(message: BaseMessage | undefined): boolean {
  return typeof message?._getType === 'function' && message._getType() === 'ai'
}

function isVisibleAssistantMessage(
  message: (VisibleMessageWithId & { readonly role?: unknown }) | undefined,
): boolean {
  return isRecord(message) && message.role === 'assistant'
}

function messageContentEqualsText(message: BaseMessage | undefined, text: string): boolean {
  if (!message) return false
  return textContentFromMessageContent(message.content) === text
}

function pendingEditHydratedMessage(
  messages: readonly BaseMessage[],
  pendingEdit: PendingEditRenderState,
): BaseMessage | undefined {
  const targetIndex = pendingEditTargetIndex(messages, pendingEdit)
  const targetMessage = targetIndex >= 0 ? messages[targetIndex] : undefined
  if (messageContentEqualsText(targetMessage, pendingEdit.content)) return targetMessage
  return messages.find(
    (message) => isHumanMessage(message) && messageContentEqualsText(message, pendingEdit.content),
  )
}

function branchMetadataFromMessage(
  message: BaseMessage | undefined,
): Record<string, unknown> | null {
  const additionalKwargs = isRecord(message?.additional_kwargs) ? message.additional_kwargs : {}
  const metadata = isRecord(additionalKwargs.metadata) ? additionalKwargs.metadata : null
  return metadata
}

function pendingEditHydrationIsReady(state: unknown, pendingEdit: PendingEditRenderState): boolean {
  const messages = messagesFromThreadState(state)
  if (!messages) return false
  const targetMessage = pendingEditHydratedMessage(messages, pendingEdit)
  if (!targetMessage) return false
  const targetIndex = messages.findIndex((message) => message === targetMessage)
  if (targetIndex < 0 || targetIndex >= messages.length - 1) return false
  const metadata = branchMetadataFromMessage(targetMessage)
  const branchIndex = metadata?.branchIndex
  const branchTotal = metadata?.branchTotal
  if (pendingEdit.requiresLatestBranchMetadata) {
    return (
      typeof branchTotal === 'number' &&
      branchTotal >= 2 &&
      typeof branchIndex === 'number' &&
      branchIndex === branchTotal - 1
    )
  }
  if (typeof branchTotal === 'number' && branchTotal >= 2) {
    return typeof branchIndex === 'number' && branchIndex === branchTotal - 1
  }
  return true
}

function pendingReloadHydrationIsReady(
  state: unknown,
  pendingReload: PendingReloadRenderState,
): boolean {
  const messages = messagesFromThreadState(state)
  if (!messages) return false
  const targetIndex = pendingReloadTargetIndex(messages, pendingReload)
  if (targetIndex < 0) return false
  const targetMessage = messages[targetIndex]
  return (
    !!targetMessage &&
    isAssistantMessage(targetMessage) &&
    messageRenderKey(targetMessage) !== pendingReload.staleMessageKey &&
    !isEmptyAssistantMessage(targetMessage) &&
    pendingReloadAssistantBranchHydrationIsReady(messages, pendingReload) &&
    pendingReloadUserBranchHydrationIsReady(messages, pendingReload)
  )
}

function pendingReloadAssistantBranchHydrationIsReady(
  messages: readonly BaseMessage[],
  pendingReload: PendingReloadRenderState,
): boolean {
  const required = pendingReload.requiredAssistantBranch
  const message = messages[required.index]
  if (!isAssistantMessage(message)) return false
  const metadata = branchMetadataFromMessage(message)
  const branchIndex = metadata?.branchIndex
  const branchTotal = metadata?.branchTotal
  return (
    typeof branchTotal === 'number' &&
    branchTotal >= required.branchTotal + 1 &&
    typeof branchIndex === 'number' &&
    branchIndex === branchTotal - 1
  )
}

function pendingReloadUserBranchHydrationIsReady(
  messages: readonly BaseMessage[],
  pendingReload: PendingReloadRenderState,
): boolean {
  const required = pendingReload.requiredUserBranch
  if (!required) return true
  const message =
    (required.id ? messages.find((candidate) => candidate.id === required.id) : undefined) ??
    messages[required.index]
  if (!isHumanMessage(message)) return false
  const metadata = branchMetadataFromMessage(message)
  const branchIndex = metadata?.branchIndex
  const branchTotal = metadata?.branchTotal
  return (
    typeof branchTotal === 'number' &&
    branchTotal >= required.branchTotal &&
    typeof branchIndex === 'number' &&
    branchIndex === branchTotal - 1
  )
}

function textContentFromMessageContent(content: BaseMessage['content']): string | null {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return null
  const parts: string[] = []
  for (const part of content) {
    if (typeof part === 'string') {
      parts.push(part)
      continue
    }
    if (!isRecord(part)) return null
    const text = part.text
    if (typeof text !== 'string') return null
    parts.push(text)
  }
  return parts.join('')
}

function appendMessageText(message: {
  content: readonly unknown[]
  attachments?: readonly { content?: readonly unknown[] }[]
}): string {
  const content = [
    ...message.content,
    ...(message.attachments?.flatMap((attachment) => attachment.content) ?? []),
  ]
  return content
    .map((part) => {
      if (typeof part === 'string') return part
      if (!isRecord(part)) return ''
      return typeof part.text === 'string' ? part.text : ''
    })
    .join('')
}

function appendMessageHasAttachments(message: { attachments?: readonly unknown[] }): boolean {
  return Array.isArray(message.attachments) && message.attachments.length > 0
}

function suppressPendingEditConvertedDuplicate(
  messages: readonly ConvertedMessage[],
  pendingEdit: PendingEditRenderState | null,
): readonly ConvertedMessage[] {
  if (!pendingEdit) return messages
  const targetIndex = pendingEditConvertedTargetIndex(messages, pendingEdit)
  if (targetIndex < 0) return messages
  const duplicateIndex = pendingEditConvertedDuplicateIndex(messages, pendingEdit, targetIndex)
  if (duplicateIndex <= targetIndex) return messages
  return [...messages.slice(0, duplicateIndex), ...messages.slice(duplicateIndex + 1)]
}

function suppressPendingEditConvertedStaleTail(
  messages: readonly ConvertedMessage[],
  pendingEdit: PendingEditRenderState | null,
): readonly ConvertedMessage[] {
  if (!pendingEdit) return messages
  const staleTailContentFingerprints =
    pendingEdit.staleConvertedTailContentFingerprints.length > 0
      ? pendingEdit.staleConvertedTailContentFingerprints
      : pendingEdit.staleTailContentFingerprints
  if (staleTailContentFingerprints.length === 0) return messages
  const targetIndex = pendingEditConvertedTargetIndex(messages, pendingEdit)
  if (targetIndex < 0) return messages
  const tail = messages.slice(targetIndex + 1)
  const stalePrefixLength = convertedStaleTailPrefixLength(tail, staleTailContentFingerprints)
  if (stalePrefixLength <= 0) return messages
  return [
    ...messages.slice(0, targetIndex + 1),
    ...tail.slice(stalePrefixLength),
  ] as readonly ConvertedMessage[]
}

function convertedStaleTailPrefixLength(
  tail: readonly ConvertedMessage[],
  staleTailContentFingerprints: readonly string[],
): number {
  const limit = Math.min(tail.length, staleTailContentFingerprints.length)
  let index = 0
  while (
    index < limit &&
    convertedMessageContentRoleFingerprint(tail[index]) === staleTailContentFingerprints[index]
  ) {
    index += 1
  }
  return index
}

function applyPendingEditConvertedBranchMetadata(
  messages: readonly ConvertedMessage[],
  pendingEdit: PendingEditRenderState | null,
): readonly ConvertedMessage[] {
  if (!pendingEdit) return messages
  const targetIndex = pendingEditConvertedTargetIndex(messages, pendingEdit)
  if (targetIndex < 0) return messages
  const targetMessage = messages[targetIndex]
  if (!targetMessage) return messages
  if (convertedMessageText(targetMessage) !== pendingEdit.content) return messages
  const replacement = cloneConvertedMessageWithPendingEditBranchMetadata(targetMessage, pendingEdit)
  if (!replacement || replacement === targetMessage) return messages
  return [...messages.slice(0, targetIndex), replacement, ...messages.slice(targetIndex + 1)]
}

function cloneConvertedMessageWithPendingEditBranchMetadata(
  message: ConvertedMessage | undefined,
  pendingEdit: PendingEditRenderState,
): ConvertedMessage | undefined {
  if (!isRecord(message)) return message
  const metadata: Record<string, unknown> = isRecord(message.metadata) ? message.metadata : {}
  const customValue = metadata.custom
  const custom = isRecord(customValue) ? customValue : {}
  const nextCustom =
    pendingEdit.pendingBranchTotal !== null
      ? pendingBranchPickerCustomMetadata(custom, pendingEdit.pendingBranchTotal, 'pending-edit')
      : clearedBranchPickerCustomMetadata(custom)
  const nextMetadata = {
    ...metadata,
    custom: nextCustom,
  }
  if (stableString(nextMetadata) === stableString(message.metadata)) return message
  return {
    ...message,
    metadata: nextMetadata,
  } as ConvertedMessage
}

function pendingEditConvertedTargetIndex(
  messages: readonly ConvertedMessage[],
  pendingEdit: Pick<
    PendingCheckpointEditSubmit,
    'parentId' | 'sourceId' | 'targetId' | 'targetIndex'
  >,
): number {
  for (const candidateId of [pendingEdit.sourceId, pendingEdit.parentId, pendingEdit.targetId]) {
    if (!candidateId) continue
    const index = messages.findIndex((message) => convertedMessageId(message) === candidateId)
    if (index >= 0) return index
  }
  if (
    pendingEdit.targetIndex != null &&
    isConvertedUserMessage(messages[pendingEdit.targetIndex])
  ) {
    return pendingEdit.targetIndex
  }
  return -1
}

function pendingEditConvertedDuplicateIndex(
  messages: readonly ConvertedMessage[],
  pendingEdit: PendingEditRenderState,
  targetIndex: number,
): number {
  for (let index = messages.length - 1; index > targetIndex; index -= 1) {
    const message = messages[index]
    if (!isConvertedUserMessage(message)) continue
    const text = convertedMessageText(message)
    if (text === '' || text === pendingEdit.content) return index
  }
  return -1
}

function appendPendingNewSubmitMessage(
  messages: readonly BaseMessage[],
  pendingNewSubmit: PendingNewSubmitState | null,
): readonly BaseMessage[] {
  if (!pendingNewSubmit) return messages
  const alreadyVisible = messages.some(
    (message) =>
      isHumanMessage(message) && messageContentEqualsText(message, pendingNewSubmit.content),
  )
  if (alreadyVisible) return messages
  const insertionIndex = Math.min(pendingNewSubmit.baseMessageCount, messages.length)
  return [
    ...messages.slice(0, insertionIndex),
    pendingNewSubmit.message,
    ...messages.slice(insertionIndex),
  ]
}

function convertedMessageId(message: ConvertedMessage | undefined): string | null {
  if (!isRecord(message)) return null
  return typeof message.id === 'string' ? message.id : null
}

function isConvertedUserMessage(message: ConvertedMessage | undefined): boolean {
  return isRecord(message) && message.role === 'user'
}

function convertedMessageContentRoleFingerprint(message: ConvertedMessage | undefined): string {
  const role = isRecord(message) && typeof message.role === 'string' ? message.role : null
  return stableString({
    type: role === 'user' ? 'human' : role === 'assistant' ? 'ai' : role,
    content: convertedMessageComparableContent(message),
  })
}

function convertedMessageText(message: ConvertedMessage | undefined): string | null {
  if (!isRecord(message)) return null
  return textFromUnknownContent(message.content)
}

function convertedMessageComparableContent(message: ConvertedMessage | undefined): unknown {
  if (!isRecord(message)) return null
  const text = convertedMessageText(message)
  return text !== null ? text : message.content
}

function textFromUnknownContent(content: unknown): string | null {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return null
  const parts: string[] = []
  for (const part of content) {
    if (typeof part === 'string') {
      parts.push(part)
      continue
    }
    if (!isRecord(part)) return null
    const text = part.text
    if (typeof text !== 'string') return null
    parts.push(text)
  }
  return parts.join('')
}

function messageContentFingerprint(message: BaseMessage | undefined): string {
  if (!message) return ''
  return stableString({
    type: typeof message._getType === 'function' ? message._getType() : undefined,
    name: message.name,
    content: message.content,
  })
}

function messageContentRoleFingerprint(message: BaseMessage | undefined): string {
  if (!message) return ''
  return stableString({
    type: typeof message._getType === 'function' ? message._getType() : undefined,
    content: textContentFromMessageContent(message.content),
  })
}

function messageListFingerprint(messages: readonly BaseMessage[]): string {
  return stableString(messages.map(messageRenderKey))
}

function messageRenderKey(message: BaseMessage): string {
  const metadata = branchMetadataFromMessage(message)
  const source = message as SnapshotCloneableMessage
  return stableString({
    id: message.id ?? null,
    checkpointId: metadata?.checkpoint_id ?? null,
    branchCheckpointId: metadata?.branchCheckpointId ?? null,
    fingerprint: messageContentFingerprint(message),
    status: source.status,
    tool_calls: source.tool_calls,
    invalid_tool_calls: source.invalid_tool_calls,
  })
}

function lastAssistantMessageIndex(messages: readonly BaseMessage[]): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (isAssistantMessage(messages[index])) return index
  }
  return -1
}

function lastHumanMessageIndex(messages: readonly BaseMessage[]): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (isHumanMessage(messages[index])) return index
  }
  return -1
}

function reloadPromptMessageKey(
  messages: readonly BaseMessage[],
  targetIndex: number,
): string | null {
  for (let index = targetIndex - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (isHumanMessage(message)) return reloadMessageKey(message)
  }
  return null
}

function reloadMessageKey(message: BaseMessage): string {
  return stableString({
    id: message.id ?? null,
    fingerprint: messageContentFingerprint(message),
  })
}

function messageListsSharePrefix(
  left: readonly BaseMessage[],
  right: readonly BaseMessage[],
  length: number,
): boolean {
  return messageListsSharedPrefixLength(left, right) >= length
}

function messageListsSharedPrefixLength(
  left: readonly BaseMessage[],
  right: readonly BaseMessage[],
): number {
  const length = Math.min(left.length, right.length)
  let index = 0
  while (
    index < length &&
    messageContentFingerprint(left[index]) === messageContentFingerprint(right[index])
  ) {
    index += 1
  }
  return index
}

function messageListIsOrderedSubsequence(
  candidate: readonly BaseMessage[],
  cached: readonly BaseMessage[],
): boolean {
  let cachedIndex = 0
  for (const candidateMessage of candidate) {
    let found = false
    while (cachedIndex < cached.length) {
      if (messagesShareStableIdentity(candidateMessage, cached[cachedIndex])) {
        found = true
        cachedIndex += 1
        break
      }
      cachedIndex += 1
    }
    if (!found) return false
  }
  return true
}

function messagesShareStableIdentity(left: BaseMessage, right: BaseMessage | undefined): boolean {
  if (!right) return false
  const leftIdentity = baseMessageIdentity(left)
  const rightIdentity = baseMessageIdentity(right)
  if (leftIdentity && rightIdentity) return leftIdentity === rightIdentity
  return messageContentFingerprint(left) === messageContentFingerprint(right)
}

function messageListIsDegraded(
  candidate: readonly BaseMessage[],
  cached: readonly BaseMessage[],
): boolean {
  if (candidate.length < cached.length) {
    if (messageListsSharePrefix(candidate, cached, candidate.length)) return true
    return messageListIsOrderedSubsequence(candidate, cached)
  }
  if (candidate.length !== cached.length || candidate.length === 0) return false

  const lastIndex = candidate.length - 1
  const candidateLast = candidate[lastIndex]
  const cachedLast = cached[lastIndex]
  return (
    messageListsSharePrefix(candidate, cached, lastIndex) &&
    isEmptyAssistantMessage(candidateLast) &&
    !isEmptyAssistantMessage(cachedLast)
  )
}

function postRunHydrationIsReady(
  stateMessages: readonly BaseMessage[],
  visibleMessages: readonly BaseMessage[],
): boolean {
  if (!hasReadyAssistantAfterLastHumanMessage(stateMessages)) return false
  if (visibleMessages.length === 0) return true
  if (stateMessages.length < visibleMessages.length) return false
  if (messageListIsDegraded(stateMessages, visibleMessages)) return false
  return humanMessageCount(stateMessages) >= humanMessageCount(visibleMessages)
}

function humanMessageCount(messages: readonly BaseMessage[]): number {
  return messages.filter(isHumanMessage).length
}

function mergeCachedReadyAssistantMessages(
  candidate: readonly BaseMessage[],
  cached: readonly BaseMessage[],
): readonly BaseMessage[] {
  if (candidate.length === 0 || cached.length === 0) return candidate

  let changed = false
  const merged = candidate.map((message, index) => {
    const cachedMessage = cached[index]
    if (!cachedMessage) return message
    if (!canReuseCachedReadyAssistantMessage(message, cachedMessage)) return message
    changed = true
    return cachedMessage
  })
  return changed ? merged : candidate
}

function canReuseCachedReadyAssistantMessage(candidate: BaseMessage, cached: BaseMessage): boolean {
  if (!isAssistantMessage(candidate) || !isAssistantMessage(cached)) return false
  return isEmptyAssistantMessage(candidate) && !isEmptyAssistantMessage(cached)
}

function isEmptyAssistantMessage(message: BaseMessage | undefined): boolean {
  if (!message || typeof message._getType !== 'function' || message._getType() !== 'ai') {
    return false
  }
  if (assistantMessageHasToolCalls(message)) return false
  return isEmptyMessageContent(message.content)
}

function suppressRunningEmptyAssistantPlaceholder(
  messages: readonly BaseMessage[],
  isRunning: boolean,
): readonly BaseMessage[] {
  if (!isRunning) return messages
  return isEmptyAssistantMessage(messages.at(-1)) ? messages.slice(0, -1) : messages
}

function assistantMessageHasToolCalls(message: BaseMessage): boolean {
  const source = message as BaseMessage & {
    readonly additional_kwargs?: unknown
    readonly invalid_tool_calls?: unknown
    readonly tool_calls?: unknown
  }
  if (Array.isArray(source.tool_calls) && source.tool_calls.length > 0) return true
  if (Array.isArray(source.invalid_tool_calls) && source.invalid_tool_calls.length > 0) return true
  const additionalKwargs = isRecord(source.additional_kwargs) ? source.additional_kwargs : {}
  const additionalToolCalls = additionalKwargs.tool_calls
  return Array.isArray(additionalToolCalls) && additionalToolCalls.length > 0
}

function isEmptyMessageContent(content: BaseMessage['content']): boolean {
  if (typeof content === 'string') return content.length === 0
  if (Array.isArray(content)) {
    return content.length === 0 || content.every(isEmptyTextContentPart)
  }
  return false
}

function isEmptyTextContentPart(value: unknown): boolean {
  if (!isRecord(value)) return false
  if (value.type !== 'text') return false
  const text = value.text
  return text === undefined || text === ''
}

function useStickyConversationMessages(
  conversationId: string,
  messages: readonly BaseMessage[],
  replaceMessages: boolean,
): readonly BaseMessage[] {
  const stickyMessages = useMemo(() => {
    const cached = stickyMessagesByConversation.get(conversationId)
    const contentMergedMessages = cached
      ? mergeCachedNonEmptyMessageContent(messages, cached)
      : messages
    const mergedMessages =
      !replaceMessages && cached
        ? mergeCachedReadyAssistantMessages(
            mergeCachedPrefixForAppendOnlyStream(contentMergedMessages, cached),
            cached,
          )
        : contentMergedMessages
    if (!replaceMessages && cached && messageListIsDegraded(mergedMessages, cached)) return cached
    return mergedMessages
  }, [conversationId, messages, replaceMessages])

  useLayoutEffect(() => {
    const cached = stickyMessagesByConversation.get(conversationId)
    const contentMergedMessages = cached
      ? mergeCachedNonEmptyMessageContent(messages, cached)
      : messages
    const mergedMessages =
      !replaceMessages && cached
        ? mergeCachedReadyAssistantMessages(
            mergeCachedPrefixForAppendOnlyStream(contentMergedMessages, cached),
            cached,
          )
        : contentMergedMessages
    if (!replaceMessages && cached && messageListIsDegraded(mergedMessages, cached)) return
    cacheStickyMessages(conversationId, mergedMessages)
  }, [conversationId, messages, replaceMessages])

  return stickyMessages
}

function mergeCachedNonEmptyMessageContent(
  candidate: readonly BaseMessage[],
  cached: readonly BaseMessage[],
): readonly BaseMessage[] {
  if (candidate.length === 0 || cached.length === 0) return candidate

  let changed = false
  const usedCachedIndexes = new Set<number>()
  const merged = candidate.map((message, index) => {
    const cachedMessage = reusableCachedContentMessage(message, cached, index, usedCachedIndexes)
    if (!cachedMessage) return message
    changed = true
    return cachedMessage
  })
  return changed ? merged : candidate
}

function reusableCachedContentMessage(
  candidate: BaseMessage,
  cached: readonly BaseMessage[],
  index: number,
  usedCachedIndexes: Set<number>,
): BaseMessage | null {
  const indexed = cached[index]
  if (
    indexed &&
    !usedCachedIndexes.has(index) &&
    canReuseCachedNonEmptyMessageContent(candidate, indexed)
  ) {
    usedCachedIndexes.add(index)
    return indexed
  }

  const identity = baseMessageIdentity(candidate)
  if (!identity) return null
  for (const [cachedIndex, cachedMessage] of cached.entries()) {
    if (usedCachedIndexes.has(cachedIndex)) continue
    if (baseMessageIdentity(cachedMessage) !== identity) continue
    if (!canReuseCachedNonEmptyMessageContent(candidate, cachedMessage)) continue
    usedCachedIndexes.add(cachedIndex)
    return cachedMessage
  }
  return null
}

function baseMessageIdentity(message: BaseMessage): string | null {
  const id = langChainMessageId(message)
  if (!id) return null
  if (isHumanMessage(message)) return `human:${id}`
  if (isAssistantMessage(message)) return `ai:${id}`
  return null
}

function canReuseCachedNonEmptyMessageContent(
  candidate: BaseMessage,
  cached: BaseMessage,
): boolean {
  if (isHumanMessage(candidate) && isHumanMessage(cached)) {
    return isEmptyMessageContent(candidate.content) && !isEmptyMessageContent(cached.content)
  }
  return canReuseCachedReadyAssistantMessage(candidate, cached)
}

function mergeCachedPrefixForAppendOnlyStream(
  candidate: readonly BaseMessage[],
  cached: readonly BaseMessage[],
): readonly BaseMessage[] {
  if (candidate.length === 0 || cached.length === 0) return candidate
  const sharedPrefixLength = messageListsSharedPrefixLength(candidate, cached)
  if (sharedPrefixLength === 0) return candidate
  if (sharedPrefixLength === candidate.length || sharedPrefixLength === cached.length) {
    return candidate
  }
  const tail = candidate.slice(sharedPrefixLength)
  const firstTailMessage = tail[0]
  if (!firstTailMessage || !isHumanMessage(firstTailMessage)) return candidate
  const uncachedTail = tail.filter((message) => !cachedContainsStableIdentity(cached, message))
  return uncachedTail.length === 0 ? cached : [...cached, ...uncachedTail]
}

function cachedContainsStableIdentity(
  cached: readonly BaseMessage[],
  candidate: BaseMessage,
): boolean {
  const identity = baseMessageIdentity(candidate)
  if (!identity) return false
  return cached.some((message) => baseMessageIdentity(message) === identity)
}

function useStickyConvertedMessages(
  conversationId: string,
  messages: readonly ConvertedMessage[],
  isRunning: boolean,
  replaceMessages: boolean,
): readonly ConvertedMessage[] {
  const stickyMessages = useMemo(() => {
    const cached = stickyConvertedMessagesByConversation.get(conversationId)
    const mergedMessages = cached
      ? mergeCachedConvertedMessages(messages, cached, { reuseReadyAssistant: !isRunning })
      : messages
    if (!replaceMessages && messages.length === 0 && cached && cached.length > 0) return cached
    if (!replaceMessages && cached && convertedMessageListIsDegraded(mergedMessages, cached)) {
      return cached
    }
    return mergedMessages
  }, [conversationId, isRunning, messages, replaceMessages])

  useLayoutEffect(() => {
    const cached = stickyConvertedMessagesByConversation.get(conversationId)
    const mergedMessages = cached
      ? mergeCachedConvertedMessages(messages, cached, { reuseReadyAssistant: !isRunning })
      : messages
    if (!replaceMessages && messages.length === 0 && cached && cached.length > 0) return
    if (!replaceMessages && cached && convertedMessageListIsDegraded(mergedMessages, cached)) return
    if (mergedMessages.length > 0) cacheStickyConvertedMessages(conversationId, mergedMessages)
  }, [conversationId, isRunning, messages, replaceMessages])

  return stickyMessages
}

function mergeCachedConvertedMessages(
  candidate: readonly ConvertedMessage[],
  cached: readonly ConvertedMessage[],
  options: { readonly reuseReadyAssistant: boolean },
): readonly ConvertedMessage[] {
  if (candidate.length === 0 || cached.length === 0) return candidate

  let changed = false
  const usedCachedIndexes = new Set<number>()
  const merged = candidate.map((message, index) => {
    const cachedMessage = reusableCachedConvertedMessage(
      message,
      cached,
      index,
      options,
      usedCachedIndexes,
    )
    if (!cachedMessage) return message
    changed = true
    return cachedMessage
  })
  return changed ? merged : candidate
}

function reusableCachedConvertedMessage(
  candidate: ConvertedMessage,
  cached: readonly ConvertedMessage[],
  index: number,
  options: { readonly reuseReadyAssistant: boolean },
  usedCachedIndexes: Set<number>,
): ConvertedMessage | null {
  const indexed = cached[index]
  if (
    indexed &&
    !usedCachedIndexes.has(index) &&
    canReuseCachedConvertedMessage(candidate, indexed, options)
  ) {
    usedCachedIndexes.add(index)
    return indexed
  }

  const identity = convertedMessageIdentity(candidate)
  if (!identity) return null

  for (const [cachedIndex, cachedMessage] of cached.entries()) {
    if (usedCachedIndexes.has(cachedIndex)) continue
    if (convertedMessageIdentity(cachedMessage) !== identity) continue
    if (!canReuseCachedConvertedMessage(candidate, cachedMessage, options)) continue
    usedCachedIndexes.add(cachedIndex)
    return cachedMessage
  }

  return null
}

function convertedMessageIdentity(message: ConvertedMessage): string | null {
  const id = convertedMessageId(message)
  if (!id) return null
  const role = convertedMessageRole(message)
  const sourceId = sourceMessageIdFromThreadMessageId(id) ?? id
  return role ? `${role}:${sourceId}` : sourceId
}

function canReuseCachedConvertedMessage(
  candidate: ConvertedMessage,
  cached: ConvertedMessage,
  options: { readonly reuseReadyAssistant: boolean },
): boolean {
  if (
    isConvertedUserMessage(candidate) &&
    isConvertedUserMessage(cached) &&
    convertedMessageIdentity(candidate) === convertedMessageIdentity(cached)
  ) {
    return isEmptyConvertedMessage(candidate) && !isEmptyConvertedMessage(cached)
  }
  if (!options.reuseReadyAssistant) return false
  if (!isConvertedAssistantMessage(candidate) || !isConvertedAssistantMessage(cached)) return false
  return isEmptyConvertedMessage(candidate) && !isEmptyConvertedMessage(cached)
}

function convertedMessageListIsDegraded(
  candidate: readonly ConvertedMessage[],
  cached: readonly ConvertedMessage[],
): boolean {
  if (candidate.length < cached.length) {
    return convertedMessagesSharePrefix(candidate, cached, candidate.length)
  }
  if (candidate.length !== cached.length || candidate.length === 0) return false

  const lastIndex = candidate.length - 1
  return (
    convertedMessagesSharePrefix(candidate, cached, lastIndex) &&
    isConvertedAssistantMessage(candidate[lastIndex]) &&
    isConvertedAssistantMessage(cached[lastIndex]) &&
    isEmptyConvertedMessage(candidate[lastIndex]) &&
    !isEmptyConvertedMessage(cached[lastIndex])
  )
}

function convertedMessagesSharePrefix(
  left: readonly ConvertedMessage[],
  right: readonly ConvertedMessage[],
  length: number,
): boolean {
  if (left.length < length || right.length < length) return false
  return left
    .slice(0, length)
    .every(
      (message, index) =>
        convertedMessageFingerprint(message) === convertedMessageFingerprint(right[index]),
    )
}

function convertedMessageFingerprint(message: ConvertedMessage | undefined): string {
  if (!isRecord(message)) return ''
  return stableString({
    id: message.id,
    role: message.role,
    content: message.content,
  })
}

function isEmptyConvertedMessage(message: ConvertedMessage | undefined): boolean {
  if (!isRecord(message)) return false
  return isEmptyConvertedContent(message.content)
}

function isEmptyConvertedContent(content: unknown): boolean {
  if (typeof content === 'string') return content.length === 0
  if (!Array.isArray(content)) return true
  if (content.length === 0) return true
  return content.every(isEmptyConvertedTextPart)
}

function isEmptyConvertedTextPart(part: unknown): boolean {
  if (typeof part === 'string') return part.length === 0
  if (!isRecord(part)) return false
  if (part.type !== 'text') return false
  const text = part.text
  return text === undefined || text === ''
}

function isConvertedAssistantMessage(message: ConvertedMessage | undefined): boolean {
  return isRecord(message) && message.role === 'assistant'
}

function visibleMessagesWithIds(
  messages: readonly ConvertedMessage[],
  sourceMessages: readonly BaseMessage[] = [],
): readonly VisibleMessageWithId[] {
  const visibleMessages: VisibleMessageWithId[] = []
  for (const [index, message] of messages.entries()) {
    if (!('id' in message)) continue
    if (typeof message.id !== 'string' || message.id.length === 0) continue
    const role = convertedMessageRole(message) ?? langChainMessageRole(sourceMessages[index])
    const sourceId =
      sourceMessageIdFromThreadMessageId(message.id) ?? langChainMessageId(sourceMessages[index])
    visibleMessages.push({
      id: message.id,
      ...(role ? { role } : {}),
      ...(sourceId && sourceId !== message.id ? { sourceId } : {}),
    })
  }
  return visibleMessages
}

function convertedMessageRole(message: ConvertedMessage): string | null {
  if (!isRecord(message)) return null
  return typeof message.role === 'string' && message.role.length > 0 ? message.role : null
}

function langChainMessageRole(message: BaseMessage | undefined): string | null {
  if (!message || typeof message._getType !== 'function') return null
  const type = message._getType()
  if (type === 'human') return 'user'
  if (type === 'ai') return 'assistant'
  return type
}

function langChainMessageId(message: BaseMessage | undefined): string | null {
  return typeof message?.id === 'string' && message.id.length > 0 ? message.id : null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function useMoldyLangGraphStream({
  agentId,
  conversationId,
  feedbackAdapter,
  attachmentAdapter,
  onBeforeSubmit,
  onRunStartAccepted,
  serverMessages,
}: UseMoldyLangGraphStreamOptions) {
  const setChatCancelInFlight = useSetAtom(chatCancelInFlightAtom)
  const setPendingBranchPickerSuppression = useSetAtom(pendingEditBranchPickerSuppressionAtom)
  const tPage = useTranslations('chat.page')
  const tReconnect = useTranslations('chat.reconnect')
  const [threadRunNoticeState, setThreadRunNoticeState] = useState<ThreadRunNoticeSnapshot | null>(
    null,
  )
  const threadRunNotice =
    threadRunNoticeState?.conversationId === conversationId ? threadRunNoticeState.notice : null
  const setThreadRunNotice = useCallback(
    (notice: ThreadRunNotice | null) => {
      setThreadRunNoticeState({ conversationId, notice })
    },
    [conversationId],
  )
  const [serverMessageMetadataState, setServerMessageMetadataState] =
    useState<ServerMessageMetadataState | null>(null)
  const serverMessageMetadata =
    serverMessageMetadataState?.conversationId === conversationId
      ? serverMessageMetadataState.snapshot
      : EMPTY_SERVER_MESSAGE_METADATA
  const setServerMessageMetadata = useCallback(
    (snapshot: ServerMessageMetadataSnapshot) => {
      setServerMessageMetadataState({ conversationId, snapshot })
    },
    [conversationId],
  )
  const [serverStateMessages, setServerStateMessages] =
    useState<ServerStateMessagesSnapshot | null>(null)
  const [serverInterruptsState, setServerInterruptsState] =
    useState<ServerInterruptsSnapshot | null>(null)
  const serverInterrupts =
    serverInterruptsState?.conversationId === conversationId ? serverInterruptsState.interrupts : []
  const latestVisibleMessagesRef = useRef<readonly BaseMessage[]>([])
  const pendingEditBaseMessagesRef = useRef<readonly BaseMessage[] | null>(null)
  const pendingEditBaseConvertedMessagesRef = useRef<readonly ConvertedMessage[] | null>(null)
  const clearServerHydrationState = useCallback(() => {
    setServerStateMessages(null)
    setServerMessageMetadataState(null)
    setServerInterruptsState(null)
  }, [])
  const [pendingEditRenderState, setPendingEditRenderState] =
    useState<PendingEditRenderState | null>(null)
  const setPendingEditRender = useCallback(
    (next: PendingEditRenderState | null) => setPendingEditRenderState(next),
    [],
  )
  const pendingEditRender = activePendingEditRenderState(conversationId, pendingEditRenderState)
  const [pendingReloadRenderState, setPendingReloadRenderState] =
    useState<PendingReloadRenderState | null>(null)
  const setPendingReloadRender = useCallback(
    (next: PendingReloadRenderState | null) => setPendingReloadRenderState(next),
    [],
  )
  const pendingReloadRender = activePendingReloadRenderState(
    conversationId,
    pendingReloadRenderState,
  )
  const clearPendingEditRenderState = useCallback(() => {
    pendingEditBaseMessagesRef.current = null
    pendingEditBaseConvertedMessagesRef.current = null
    setPendingBranchPickerSuppression(null)
    setPendingEditRender(null)
  }, [setPendingBranchPickerSuppression, setPendingEditRender])
  const clearPendingReloadRenderState = useCallback(() => {
    setPendingReloadRender(null)
  }, [setPendingReloadRender])
  const getPendingNewSubmitSnapshot = useCallback(
    () => pendingNewSubmitsByConversation.get(conversationId) ?? null,
    [conversationId],
  )
  const cachedPendingNewSubmit = useSyncExternalStore(
    subscribePendingNewSubmitStore,
    getPendingNewSubmitSnapshot,
    getEmptyPendingNewSubmitSnapshot,
  )
  const handleThreadState = useCallback(
    (state: unknown, options?: ThreadStateHydrationOptions) => {
      setThreadRunNotice(terminalRunNoticeFromThreadState(state))
      setServerMessageMetadata(messageMetadataFromThreadState(state))
      const interrupts = interruptsFromThreadState(state)
      setServerInterruptsState({ conversationId, interrupts })
      if (options?.replaceMessages || interrupts.length > 0) {
        const messages = messagesFromThreadState(state)
        setServerStateMessages(messages ? { conversationId, messages } : null)
      }
    },
    [conversationId, setServerMessageMetadata, setThreadRunNotice],
  )
  const transport = useMemo(
    () =>
      createMoldyAgentTransport(conversationId, agentId, {
        onState: handleThreadState,
      }),
    [agentId, conversationId, handleThreadState],
  )
  useEffect(() => {
    transport.setRunStartAcceptedListener(onRunStartAccepted)
    return () => transport.setRunStartAcceptedListener(undefined)
  }, [onRunStartAccepted, transport])
  const stream = useStream<MoldyGraphState>({
    transport,
    threadId: conversationId,
  })
  useEffect(() => {
    if (!PERSISTED_CONVERSATION_ID_PATTERN.test(conversationId)) return undefined
    let cancelled = false
    void loadServerThreadState(conversationId)
      .then((state: ThreadStateResponse) => {
        if (!cancelled) handleThreadState(state)
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [conversationId, handleThreadState])
  useEffect(() => {
    const moldyTransport = transport as Partial<MoldyAgentServerAdapter>
    if (typeof moldyTransport.activateStateHydration !== 'function') return undefined
    return moldyTransport.activateStateHydration()
  }, [transport])
  const activityEvents = useChannel(stream, ACTIVITY_CHANNELS, undefined, { bufferSize: 300 })
  const activities = useMemo(
    () =>
      activityEvents.reduce<RunActivity[]>(
        (current, event) => reduceProtocolActivity(current, event),
        [],
      ),
    [activityEvents],
  )
  const deepAgentsState = useMemo(() => selectDeepAgentsState(stream.values ?? {}), [stream.values])
  const [resolvedInterruptsState, setResolvedInterruptsState] =
    useState<ResolvedInterruptsSnapshot | null>(null)
  const resolvedInterrupts =
    resolvedInterruptsState?.conversationId === conversationId
      ? resolvedInterruptsState.items
      : EMPTY_RESOLVED_INTERRUPTS
  const [postRunHydrationPending, setPostRunHydrationPending] = useState(false)
  const setResolvedInterrupts = useCallback(
    (
      updater: (
        current: readonly ResolvedInterruptToolCall[],
      ) => readonly ResolvedInterruptToolCall[],
    ) => {
      setResolvedInterruptsState((current) => ({
        conversationId,
        items: updater(current?.conversationId === conversationId ? current.items : []),
      }))
    },
    [conversationId],
  )
  const wasLoadingRef = useRef(false)
  const settlePostRunHydration = useCallback(() => {
    wasLoadingRef.current = false
    setPostRunHydrationPending(false)
  }, [setPostRunHydrationPending])
  useEffect(() => {
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      wasLoadingRef.current = false
      setPostRunHydrationPending(false)
    })
    return () => {
      cancelled = true
    }
  }, [conversationId])
  useEffect(() => {
    if (!pendingEditRender || stream.isLoading) return undefined
    let cancelled = false
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    const startedAt = Date.now()
    const hydrateCompletedEdit = (attempt: number): void => {
      void loadServerThreadState(conversationId)
        .then((state: ThreadStateResponse) => {
          if (cancelled) return
          const hydrationReady = pendingEditHydrationIsReady(state, pendingEditRender)
          if (!hydrationReady) {
            handleThreadState(state)
            if (Date.now() - startedAt > POST_RUN_HYDRATION_TIMEOUT_MS) {
              clearPendingEditRenderState()
              return
            }
            retryTimer = setTimeout(
              () => hydrateCompletedEdit(attempt + 1),
              PENDING_EDIT_HYDRATION_RETRY_MS,
            )
            return
          }
          handleThreadState(state, { replaceMessages: true })
          clearPendingEditRenderState()
        })
        .catch(() => {
          if (cancelled) return
          clearServerHydrationState()
          clearPendingEditRenderState()
        })
    }
    hydrateCompletedEdit(0)
    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
    }
  }, [
    clearPendingEditRenderState,
    clearServerHydrationState,
    conversationId,
    handleThreadState,
    pendingEditRender,
    stream.isLoading,
  ])
  useEffect(() => {
    if (!pendingReloadRender || stream.isLoading) return undefined
    let cancelled = false
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    const startedAt = Date.now()
    const hydrateCompletedReload = (): void => {
      void loadServerThreadState(conversationId)
        .then((state: ThreadStateResponse) => {
          if (cancelled) return
          const hydrationReady = pendingReloadHydrationIsReady(state, pendingReloadRender)
          if (!hydrationReady) {
            if (Date.now() - startedAt > POST_RUN_HYDRATION_TIMEOUT_MS) {
              clearPendingReloadRenderState()
              return
            }
            retryTimer = setTimeout(hydrateCompletedReload, PENDING_EDIT_HYDRATION_RETRY_MS)
            return
          }
          handleThreadState(state, { replaceMessages: true })
          clearPendingReloadRenderState()
        })
        .catch(() => {
          if (cancelled) return
          clearServerHydrationState()
          clearPendingReloadRenderState()
        })
    }
    hydrateCompletedReload()
    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
    }
  }, [
    clearPendingReloadRenderState,
    clearServerHydrationState,
    conversationId,
    handleThreadState,
    pendingReloadRender,
    stream.isLoading,
  ])
  useEffect(() => {
    const onBranchSwitched = (event: Event) => {
      if (!isMoldyBranchSwitchedEvent(event)) return
      if (event.detail.conversationId !== conversationId) return
      void loadServerThreadState(conversationId)
        .then((state: ThreadStateResponse) => {
          handleThreadState(state, { replaceMessages: true })
        })
        .catch(() => undefined)
    }
    window.addEventListener(MOLDY_BRANCH_SWITCHED_EVENT, onBranchSwitched)
    return () => window.removeEventListener(MOLDY_BRANCH_SWITCHED_EVENT, onBranchSwitched)
  }, [conversationId, handleThreadState])
  const rawVisibleStreamMessages =
    serverStateMessages?.conversationId === conversationId
      ? serverStateMessages.messages
      : stream.messages
  const cachedPendingNewSubmitAcknowledged =
    cachedPendingNewSubmit !== null &&
    rawVisibleStreamMessages.some(
      (message) =>
        isHumanMessage(message) &&
        messageContentEqualsText(message, cachedPendingNewSubmit.content),
    )
  const pendingNewSubmit = cachedPendingNewSubmitAcknowledged ? null : cachedPendingNewSubmit
  useEffect(() => {
    if (!cachedPendingNewSubmit) return
    const acknowledged = rawVisibleStreamMessages.some(
      (message) =>
        isHumanMessage(message) &&
        messageContentEqualsText(message, cachedPendingNewSubmit.content),
    )
    if (!acknowledged) return
    clearPendingNewSubmit(conversationId, cachedPendingNewSubmit.content)
  }, [cachedPendingNewSubmit, conversationId, rawVisibleStreamMessages])
  const visibleStreamMessagesWithPendingSubmit = useMemo(
    () => appendPendingNewSubmitMessage(rawVisibleStreamMessages, pendingNewSubmit),
    [pendingNewSubmit, rawVisibleStreamMessages],
  )
  const visibleStreamMessages = useMemo(
    () =>
      applyPendingReloadRenderState(
        applyPendingEditRenderState(visibleStreamMessagesWithPendingSubmit, pendingEditRender),
        pendingReloadRender,
      ),
    [pendingEditRender, pendingReloadRender, visibleStreamMessagesWithPendingSubmit],
  )
  const serverLangChainMessages = useMemo(
    () => messagesFromServerMessages(serverMessages),
    [serverMessages],
  )
  const streamStateIsSettling = stream.isLoading || postRunHydrationPending
  const visibleStreamMessagesWithFallback = useMemo(() => {
    if (streamStateIsSettling || serverLangChainMessages.length === 0) return visibleStreamMessages
    if (messageListIsDegraded(visibleStreamMessages, serverLangChainMessages)) {
      return serverLangChainMessages
    }
    return visibleStreamMessages
  }, [serverLangChainMessages, streamStateIsSettling, visibleStreamMessages])
  const renderableStreamMessages = useMemo(
    () =>
      suppressRunningEmptyAssistantPlaceholder(visibleStreamMessagesWithFallback, stream.isLoading),
    [stream.isLoading, visibleStreamMessagesWithFallback],
  )
  const streamMessagesWithServerMetadata = useMemo(() => {
    return applyPendingReloadBranchMetadata(
      applyPendingEditBranchMetadata(
        mergeServerMessageMetadata(renderableStreamMessages, serverMessageMetadata),
        pendingEditRender,
      ),
      pendingReloadRender,
    )
  }, [pendingEditRender, pendingReloadRender, renderableStreamMessages, serverMessageMetadata])
  const allInterruptPayloads = standardPayloadsFromInterrupts([
    ...serverInterrupts,
    ...stream.interrupts,
    ...threadInterruptsFromStream(stream),
  ])
  const interruptPayloads = useMemo(
    () =>
      activeInterruptPayloads(
        allInterruptPayloads,
        streamMessagesWithServerMetadata,
        resolvedInterrupts,
      ),
    [allInterruptPayloads, resolvedInterrupts, streamMessagesWithServerMetadata],
  )
  const interruptPayloadsById = useMemo(
    () => new Map(interruptPayloads.map((payload) => [payload.interrupt_id, payload])),
    [interruptPayloads],
  )
  const allInterruptPayloadsById = useMemo(
    () => new Map(allInterruptPayloads.map((payload) => [payload.interrupt_id, payload])),
    [allInterruptPayloads],
  )
  const messagesWithInterrupts = useMemo(
    () =>
      dedupeLangChainMessagesById(
        appendResolvedInterruptToolCallMessages(
          appendInterruptToolCallMessages(streamMessagesWithServerMetadata, interruptPayloads),
          resolvedInterrupts,
        ),
      ),
    [streamMessagesWithServerMetadata, interruptPayloads, resolvedInterrupts],
  )
  const messagesWithArtifacts = useLangGraphArtifactEffects({
    stream,
    conversationId,
    messages: messagesWithInterrupts,
  })
  const messagesWithUsage = useLangGraphUsageEffects({
    conversationId,
    stream,
    messages: messagesWithArtifacts,
    stateMessages: stream.values?.messages ?? [],
  })
  const terminalNoticeText =
    threadRunNotice?.status === 'stale' ? tReconnect('stale') : tPage('canceled')
  const messagesWithTerminalNotice = useMemo(
    () => appendTerminalRunNotice(messagesWithUsage, threadRunNotice, terminalNoticeText),
    [messagesWithUsage, terminalNoticeText, threadRunNotice],
  )
  const stickyMessagesWithTerminalNotice = useStickyConversationMessages(
    conversationId,
    messagesWithTerminalNotice,
    serverStateMessages?.conversationId === conversationId ||
      pendingEditRender !== null ||
      pendingReloadRender !== null,
  )
  useLayoutEffect(() => {
    latestVisibleMessagesRef.current = snapshotBaseMessages(stickyMessagesWithTerminalNotice)
  }, [stickyMessagesWithTerminalNotice])
  useEffect(() => {
    if (stream.isLoading) {
      wasLoadingRef.current = true
      queueMicrotask(() => setPostRunHydrationPending(false))
      return undefined
    }
    if (!wasLoadingRef.current) return undefined
    if (pendingEditRender || pendingReloadRender) return undefined

    let cancelled = false
    let retryTimer: ReturnType<typeof setTimeout> | null = null
    const startedAt = Date.now()
    queueMicrotask(() => {
      if (!cancelled) setPostRunHydrationPending(true)
    })
    const retryHydrationOrSettle = (): void => {
      if (Date.now() - startedAt <= POST_RUN_HYDRATION_TIMEOUT_MS) {
        retryTimer = setTimeout(hydrateCompletedRun, POST_RUN_HYDRATION_RETRY_MS)
        return
      }
      settlePostRunHydration()
    }
    const hydrateCompletedRun = (): void => {
      void loadServerThreadState(conversationId)
        .then((state: ThreadStateResponse) => {
          if (cancelled) return
          const stateMessages = messagesFromThreadState(state)
          if (
            stateMessages &&
            postRunHydrationIsReady(stateMessages, latestVisibleMessagesRef.current)
          ) {
            handleThreadState(state, { replaceMessages: true })
            settlePostRunHydration()
            return
          }
          handleThreadState(state)
          retryHydrationOrSettle()
        })
        .catch(() => {
          if (cancelled) return
          retryHydrationOrSettle()
        })
    }
    hydrateCompletedRun()
    return () => {
      cancelled = true
      if (retryTimer) clearTimeout(retryTimer)
    }
  }, [
    conversationId,
    handleThreadState,
    pendingEditRender,
    pendingReloadRender,
    settlePostRunHydration,
    stream.isLoading,
  ])
  const conversionSourceFingerprint = messageListFingerprint(stickyMessagesWithTerminalNotice)
  const conversionCallback = useMemo<typeof convertMoldyLangChainMessage>(() => {
    const conversionEpoch = conversionSourceFingerprint
    return (message, metadata) => {
      void conversionEpoch
      return convertMoldyLangChainMessage(message, metadata)
    }
  }, [conversionSourceFingerprint])
  const conversionSourceMessages = useMemo(
    () => [...stickyMessagesWithTerminalNotice],
    [stickyMessagesWithTerminalNotice],
  )
  useLangGraphMemoryEffects({ stream })
  const isRunning =
    stream.isLoading && interruptPayloads.length === 0 && threadRunNotice?.status !== 'stale'
  const runtimeIsRunning =
    isRunning ||
    postRunHydrationPending ||
    pendingEditRender !== null ||
    pendingReloadRender !== null
  const convertedMessages = useExternalMessageConverter({
    callback: conversionCallback,
    messages: conversionSourceMessages,
    isRunning,
  })
  const stableMessages = useStableConvertedMessages(
    convertedMessages,
    stickyMessagesWithTerminalNotice,
    isRunning,
  )
  const stableMessagesWithoutPendingEditBranchMetadata = useMemo(
    () => applyPendingEditConvertedBranchMetadata(stableMessages, pendingEditRender),
    [pendingEditRender, stableMessages],
  )
  const stableMessagesWithoutPendingEditStaleTail = useMemo(
    () =>
      suppressPendingEditConvertedStaleTail(
        stableMessagesWithoutPendingEditBranchMetadata,
        pendingEditRender,
      ),
    [pendingEditRender, stableMessagesWithoutPendingEditBranchMetadata],
  )
  const stableMessagesWithoutPendingEditDuplicate = useMemo(
    () =>
      suppressPendingEditConvertedDuplicate(
        stableMessagesWithoutPendingEditStaleTail,
        pendingEditRender,
      ),
    [pendingEditRender, stableMessagesWithoutPendingEditStaleTail],
  )
  const stickyRuntimeMessages = useStickyConvertedMessages(
    conversationId,
    stableMessagesWithoutPendingEditDuplicate,
    streamStateIsSettling,
    pendingEditRender !== null || pendingReloadRender !== null,
  )
  const messages = useMemo(
    () => suppressPendingEditConvertedStaleTail(stickyRuntimeMessages, pendingEditRender),
    [pendingEditRender, stickyRuntimeMessages],
  )
  const checkpointVisibleMessages = useMemo(
    () => visibleMessagesWithIds(messages, stickyMessagesWithTerminalNotice),
    [messages, stickyMessagesWithTerminalNotice],
  )
  const {
    onNew: submitNew,
    onEdit: submitEdit,
    onReload: submitReload,
  } = useCheckpointForkHandlers({
    conversationId,
    stream,
    visibleMessages: checkpointVisibleMessages,
    langChainMessages: stickyMessagesWithTerminalNotice,
    onBeforeEditSubmit: (edit) => {
      const baseMessages =
        pendingEditBaseMessagesRef.current ??
        (latestVisibleMessagesRef.current.length > 0
          ? latestVisibleMessagesRef.current
          : stickyMessagesWithTerminalNotice)
      const baseConvertedMessages = pendingEditBaseConvertedMessagesRef.current ?? messages
      setPendingEditRender(
        pendingEditRenderFromSubmit(conversationId, edit, baseMessages, baseConvertedMessages),
      )
    },
  })
  const coordinatorsRef = useRef(new Map<string, HiTLDecisionCoordinator>())
  const pendingInterruptDecisionsRef = useRef(new Map<string, Decision[]>())
  const pendingInterruptFlushRef = useRef(false)
  useEffect(() => {
    coordinatorsRef.current.clear()
    pendingInterruptDecisionsRef.current.clear()
    pendingInterruptFlushRef.current = false
  }, [conversationId])
  const adapters = useMemo(() => {
    if (!feedbackAdapter && !attachmentAdapter) return undefined
    return {
      ...(feedbackAdapter ? { feedback: feedbackAdapter } : {}),
      ...(attachmentAdapter ? { attachments: attachmentAdapter } : {}),
    }
  }, [feedbackAdapter, attachmentAdapter])
  const onCancel = useCallback(async () => {
    setChatCancelInFlight(true)
    try {
      await stream.stop()
      const activeRun = await conversationRunsApi.active(conversationId).catch(() => null)
      if (activeRun?.status === 'queued' || activeRun?.status === 'running') {
        await conversationRunsApi.cancel(conversationId, activeRun.id)
      }
      setThreadRunNotice({ id: `local-${conversationId}`, status: 'canceled' })
    } finally {
      setPendingBranchPickerSuppression(null)
      setPendingReloadRender(null)
      setChatCancelInFlight(false)
    }
  }, [
    conversationId,
    setChatCancelInFlight,
    setPendingBranchPickerSuppression,
    setPendingReloadRender,
    setThreadRunNotice,
    stream,
  ])

  const onNew = useCallback(
    async (...args: Parameters<typeof submitNew>) => {
      const content = appendMessageText(args[0]).trim()
      const hasAttachments = appendMessageHasAttachments(args[0])
      if (content.length === 0 && !hasAttachments) {
        await submitNew(...args)
        return
      }
      onBeforeSubmit?.()
      setThreadRunNotice(null)
      clearServerHydrationState()
      setPendingBranchPickerSuppression(null)
      setPendingEditRender(null)
      pendingEditBaseMessagesRef.current = null
      pendingEditBaseConvertedMessagesRef.current = null
      setPendingReloadRender(null)
      if (content.length > 0) {
        const pending: PendingNewSubmitState = {
          conversationId,
          content,
          baseMessageCount: latestVisibleMessagesRef.current.length,
          message: new HumanMessage({
            id: `moldy-pending-user:${conversationId}:${Date.now()}`,
            content,
          }),
        }
        flushSync(() => cachePendingNewSubmit(pending))
      }
      try {
        await submitNew(...args)
      } catch (caught) {
        clearPendingNewSubmit(conversationId, content)
        throw caught
      }
    },
    [
      clearServerHydrationState,
      conversationId,
      onBeforeSubmit,
      setPendingBranchPickerSuppression,
      setPendingEditRender,
      setPendingReloadRender,
      setThreadRunNotice,
      submitNew,
    ],
  )
  const onEdit = useCallback(
    async (message: AppendMessage) => {
      setThreadRunNotice(null)
      clearServerHydrationState()
      setPendingReloadRender(null)
      clearPendingNewSubmitForConversation(conversationId)
      const editBaseMessages =
        latestVisibleMessagesRef.current.length > 0
          ? latestVisibleMessagesRef.current
          : stickyMessagesWithTerminalNotice
      pendingEditBaseMessagesRef.current = editBaseMessages
      pendingEditBaseConvertedMessagesRef.current = messages
      flushSync(() =>
        setPendingEditRender(
          pendingEditRenderFromAppendMessage(
            conversationId,
            message,
            checkpointVisibleMessages,
            editBaseMessages,
            messages,
          ),
        ),
      )
      clearStickyConversationMessages(conversationId)
      try {
        const submitted = await submitEdit(message)
        if (!submitted) {
          clearPendingEditRenderState()
        }
      } catch (caught) {
        clearPendingEditRenderState()
        throw caught
      }
    },
    [
      checkpointVisibleMessages,
      clearPendingEditRenderState,
      clearServerHydrationState,
      conversationId,
      messages,
      setPendingEditRender,
      setPendingReloadRender,
      setThreadRunNotice,
      stickyMessagesWithTerminalNotice,
      submitEdit,
    ],
  )
  const onReload = useCallback(
    async (parentId: string | null) => {
      setThreadRunNotice(null)
      clearServerHydrationState()
      setPendingBranchPickerSuppression(null)
      setPendingEditRender(null)
      pendingEditBaseMessagesRef.current = null
      pendingEditBaseConvertedMessagesRef.current = null
      clearPendingNewSubmitForConversation(conversationId)
      flushSync(() =>
        setPendingReloadRender(
          pendingReloadRenderFromParentId(
            conversationId,
            parentId,
            checkpointVisibleMessages,
            stickyMessagesWithTerminalNotice,
          ),
        ),
      )
      clearStickyConversationMessages(conversationId)
      try {
        const submitted = await submitReload(parentId)
        if (!submitted) clearPendingReloadRenderState()
      } catch (caught) {
        clearPendingReloadRenderState()
        throw caught
      }
    },
    [
      checkpointVisibleMessages,
      clearPendingReloadRenderState,
      clearServerHydrationState,
      conversationId,
      setPendingBranchPickerSuppression,
      setPendingEditRender,
      setPendingReloadRender,
      setThreadRunNotice,
      stickyMessagesWithTerminalNotice,
      submitReload,
    ],
  )

  const rememberResolvedInterrupt = useCallback(
    (interruptId: string | null, decisions: readonly Decision[]) => {
      if (!interruptId) return
      const payload = allInterruptPayloadsById.get(interruptId)
      if (!payload) return
      const resolved = resolvedInterruptToolCallsFromDecisions(payload, decisions)
      if (resolved.length === 0) return
      const resolvedIds = new Set(resolved.map((item) => item.toolCall.id).filter(Boolean))
      setResolvedInterrupts((current) => [
        ...current.filter((item) => !item.toolCall.id || !resolvedIds.has(item.toolCall.id)),
        ...resolved,
      ])
    },
    [allInterruptPayloadsById, setResolvedInterrupts],
  )

  const flushPendingInterruptDecisions = useCallback(
    async (activeInterruptIds: readonly string[]): Promise<boolean> => {
      if (pendingInterruptFlushRef.current || activeInterruptIds.length === 0) return false
      const decisionsById = new Map<string, Decision[]>()
      for (const activeId of activeInterruptIds) {
        const pending = pendingInterruptDecisionsRef.current.get(activeId)
        if (!pending) return false
        decisionsById.set(activeId, pending)
      }

      pendingInterruptFlushRef.current = true
      try {
        if (activeInterruptIds.length === 1) {
          const activeId = activeInterruptIds[0]
          const decisions = decisionsById.get(activeId)
          if (!activeId || !decisions) return false
          const payload = allInterruptPayloadsById.get(activeId)
          const options = payload?.namespace
            ? { interruptId: activeId, namespace: payload.namespace }
            : { interruptId: activeId }
          await stream.respond({ decisions }, options)
          await refreshThreadLifecycleStream(stream)
          pendingInterruptDecisionsRef.current.delete(activeId)
          rememberResolvedInterrupt(activeId, decisions)
          return true
        }

        const responsesById: Record<string, { decisions: Decision[] }> = {}
        for (const [activeId, decisions] of decisionsById) {
          responsesById[activeId] = { decisions }
        }
        await stream.respondAll(responsesById)
        await refreshThreadLifecycleStream(stream)
        for (const [activeId, decisions] of decisionsById) {
          pendingInterruptDecisionsRef.current.delete(activeId)
          rememberResolvedInterrupt(activeId, decisions)
        }
        return true
      } finally {
        pendingInterruptFlushRef.current = false
      }
    },
    [allInterruptPayloadsById, rememberResolvedInterrupt, stream],
  )

  useEffect(() => {
    const activeInterruptIds = interruptPayloads.map((payload) => payload.interrupt_id)
    const active = new Set(activeInterruptIds)
    for (const key of coordinatorsRef.current.keys()) {
      if (!active.has(key)) coordinatorsRef.current.delete(key)
    }
    for (const key of pendingInterruptDecisionsRef.current.keys()) {
      if (!active.has(key)) pendingInterruptDecisionsRef.current.delete(key)
    }
    void flushPendingInterruptDecisions(activeInterruptIds).catch(() => undefined)
  }, [flushPendingInterruptDecisions, interruptPayloads])

  const assistantRuntime = useExternalStoreRuntime({
    messages,
    isRunning: runtimeIsRunning,
    adapters,
    onNew,
    onEdit,
    onReload,
    onCancel,
  })

  const sendMessage = useCallback(
    async (content: string) => {
      const trimmed = content.trim()
      if (!trimmed) return
      setThreadRunNotice(null)
      clearServerHydrationState()
      await stream.submit({ messages: [new HumanMessage(trimmed)] })
    },
    [clearServerHydrationState, setThreadRunNotice, stream],
  )
  const firstInterruptId = interruptPayloads[0]?.interrupt_id ?? null
  const onResumeDecisions = useCallback(
    async (decisions: Decision[], _displayText?: string, interruptId?: string | null) => {
      const targetId = interruptId ?? firstInterruptId
      const response = { decisions }
      const activeInterruptIds = interruptPayloads.map((payload) => payload.interrupt_id)
      if (targetId && activeInterruptIds.length > 1 && activeInterruptIds.includes(targetId)) {
        pendingInterruptDecisionsRef.current.set(targetId, [...decisions])
        await flushPendingInterruptDecisions(activeInterruptIds)
        return
      }
      if (targetId) {
        const payload = allInterruptPayloadsById.get(targetId)
        const options = payload?.namespace
          ? { interruptId: targetId, namespace: payload.namespace }
          : { interruptId: targetId }
        await stream.respond(response, options)
        await refreshThreadLifecycleStream(stream)
        rememberResolvedInterrupt(targetId, decisions)
        return
      }
      await stream.respond(response)
      await refreshThreadLifecycleStream(stream)
      rememberResolvedInterrupt(null, decisions)
    },
    [
      allInterruptPayloadsById,
      firstInterruptId,
      flushPendingInterruptDecisions,
      interruptPayloads,
      rememberResolvedInterrupt,
      stream,
    ],
  )
  const onResumeDecisionsRef = useRef(onResumeDecisions)
  useEffect(() => {
    onResumeDecisionsRef.current = onResumeDecisions
  }, [onResumeDecisions])
  const registerDecision = useCallback(
    async (
      actionIndex: number,
      decision: Decision,
      displayText?: string,
      interruptId?: string | null,
    ) => {
      const targetId = interruptId ?? firstInterruptId
      const payload = targetId ? interruptPayloadsById.get(targetId) : interruptPayloads[0]
      if (!targetId || !payload || payload.action_requests.length <= 1) {
        await onResumeDecisions([decision], displayText, targetId)
        return
      }
      const existing = coordinatorsRef.current.get(targetId)
      const coordinator =
        existing ??
        createHiTLDecisionCoordinator({
          totalActions: payload.action_requests.length,
          interruptId: targetId,
          resume: (decisions, coordinatorDisplayText, coordinatorInterruptId) =>
            onResumeDecisionsRef.current(decisions, coordinatorDisplayText, coordinatorInterruptId),
        })
      coordinatorsRef.current.set(targetId, coordinator)
      await coordinator.registerDecision(actionIndex, decision, displayText)
    },
    [firstInterruptId, interruptPayloads, interruptPayloadsById, onResumeDecisions],
  )

  return {
    stream,
    assistantRuntime,
    activities,
    deepAgentsState,
    sendMessage,
    onResumeDecisions,
    registerDecision,
  }
}
