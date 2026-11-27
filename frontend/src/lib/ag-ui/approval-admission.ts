import { hashKey } from '@tanstack/react-query'
import type { AgUiInterrupt } from '@assistant-ui/react-ag-ui'
import type { Message } from '@ag-ui/core'
import type { StandardInterruptPayload } from '@/lib/types'
import { moldyAgUiInterruptPayloads } from './chat-actions-interrupts'
import type { AgUiChatSessionState } from './chat-history'
import type { AgUiHistoryChild } from './chat-history-children'
import type { WireChatSnapshot } from './chat-history-types'

type ChildAccess =
  | { mode: 'managed'; child: AgUiHistoryChild & { childTaskId: string } }
  | { mode: 'legacy'; child: AgUiHistoryChild }

export type ChildInterruptForm = ChildAccess & {
  payload: StandardInterruptPayload
  key: string
  expiresAt?: string
}

function byUniqueId<T>(rows: readonly T[], idFor: (row: T) => string): Map<string, T | null> {
  const byId = new Map<string, T | null>()
  for (const row of rows) {
    const id = idFor(row)
    byId.set(id, byId.has(id) ? null : row)
  }
  return byId
}

function interruptBatch(slots: readonly AgUiInterrupt[]) {
  const payloads = moldyAgUiInterruptPayloads(slots)
  if (payloads.length !== slots.length) return null
  const byId = byUniqueId(payloads, (value) => value.interrupt_id)
  const entries = []
  for (const slot of slots) {
    const payload = byId.get(slot.id)
    if (!payload || (slot.expiresAt !== undefined && !(Date.parse(slot.expiresAt) > Date.now()))) {
      return null
    }
    // The resume protocol commits the entire set. Even a change in another child's
    // requested decision invalidates a form captured before that change.
    const key = hashKey([
      slot.id,
      slot.subagentRunId,
      slot.reason,
      slot.toolCallId,
      slot.expiresAt,
      slot.responseSchema,
      slot.message,
      payload,
    ])
    entries.push({ slot, payload, key })
    byId.delete(slot.id)
  }
  return entries.sort((a, b) => (a.slot.id < b.slot.id ? -1 : a.slot.id > b.slot.id ? 1 : 0))
}

function messageKey(owner: string | undefined, messageId: string) {
  return JSON.stringify([owner, messageId])
}

function issuerPath(
  child: AgUiHistoryChild,
  byChild: ReadonlyMap<string, AgUiHistoryChild | null>,
  byMessage: ReadonlyMap<string, Message | null>,
) {
  const path = []
  const seen = new Set<string>()
  let current: AgUiHistoryChild | null | undefined = child
  while (current) {
    if (seen.has(current.subagentRunId) || !current.parentMessageId || !current.parentToolCallId)
      return null
    seen.add(current.subagentRunId)
    const parent = current.parentSubagentRunId
    const message = byMessage.get(messageKey(parent, current.parentMessageId))
    const callId = current.parentToolCallId
    if (message?.role !== 'assistant') return null
    const calls = message.toolCalls?.filter((call) => call.id === callId)
    if (calls?.length !== 1 || calls[0].function.name !== 'task') return null
    path.push([current.subagentRunId, current.parentMessageId, callId])
    if (!parent) return path
    current = byChild.get(parent)
  }
  return null
}

function childAccess(child: AgUiHistoryChild, slot: AgUiInterrupt): ChildAccess | null {
  if (child.childTaskId) {
    return { mode: 'managed', child: { ...child, childTaskId: child.childTaskId } }
  }
  const proof = slot.metadata?.moldy
  return child.legacyInterruptAccess === true &&
    proof &&
    typeof proof === 'object' &&
    'legacyInterruptAccess' in proof &&
    proof.legacyInterruptAccess === true
    ? { mode: 'legacy', child }
    : null
}

function verifiedApprovalBatch(
  conversationId: string,
  wire: WireChatSnapshot,
  pending: readonly AgUiInterrupt[],
): { key: string; childForms: ChildInterruptForm[] } | null {
  if (
    wire.threadId !== conversationId ||
    wire.activeRunId ||
    wire.runStatus !== 'interrupted' ||
    !wire.checkpointId ||
    wire.runId !== wire.interruptRunId ||
    wire.latestPhysicalRunId !== wire.physicalInterruptRunId ||
    !wire.interruptRunId ||
    !wire.physicalInterruptRunId ||
    !wire.interrupts.length
  )
    return null
  const batch = interruptBatch(wire.interrupts)
  const live = interruptBatch(pending)
  if (
    !batch ||
    !live ||
    batch.length !== live.length ||
    batch.some(({ key }, index) => key !== live[index].key)
  )
    return null

  const byChild = byUniqueId(wire.subagents, (child) => child.subagentRunId)
  const byMessage = byUniqueId(wire.messages, (message) =>
    messageKey(message.subagentRunId, message.id),
  )
  const group = []
  const forms = []
  for (const { slot, payload, key } of batch) {
    if (!slot.subagentRunId) {
      group.push([key])
      continue
    }
    const child = byChild.get(slot.subagentRunId)
    if (
      !child ||
      child.status !== 'interrupted' ||
      child.runId !== wire.interruptRunId ||
      child.physicalRunId !== wire.physicalInterruptRunId ||
      !child.interruptIds?.includes(slot.id) ||
      wire.subagents.some(
        (sibling) =>
          sibling !== child &&
          ((child.childTaskId && sibling.childTaskId === child.childTaskId) ||
            sibling.interruptIds?.includes(slot.id)),
      )
    )
      return null
    const access = childAccess(child, slot)
    const path = issuerPath(child, byChild, byMessage)
    if (!access || !path) return null
    if (
      access.mode === 'legacy' &&
      (!wire.checkpointId ||
        !wire.activeTipMessageId ||
        !byMessage.get(messageKey(undefined, wire.activeTipMessageId)))
    )
      return null
    group.push([key, access.mode, child.childTaskId, path])
    forms.push({ ...access, payload, expiresAt: slot.expiresAt })
  }
  const generation = hashKey([
    conversationId,
    wire.checkpointId,
    wire.activeTipMessageId,
    wire.interruptRunId,
    wire.physicalInterruptRunId,
    group,
  ])
  return {
    key: generation,
    childForms: forms.map((form) => ({
      ...form,
      key: hashKey([generation, form.payload.interrupt_id]),
    })),
  }
}

function currentApprovalBatch(
  conversationId: string,
  state: AgUiChatSessionState,
  pending: readonly AgUiInterrupt[],
) {
  // A terminal event alone does not prove that the worker committed a pause.
  // Only refreshRepository publishes the selected snapshot and its slot array together.
  if (
    state.readDenied ||
    state.activeRunId ||
    !state.snapshot ||
    state.interrupts !== state.snapshot.interrupts
  )
    return null
  return verifiedApprovalBatch(conversationId, state.snapshot, pending)
}

export function currentAgUiApprovalKey(
  conversationId: string,
  state: AgUiChatSessionState,
  pending: readonly AgUiInterrupt[],
): string | null {
  const batch = currentApprovalBatch(conversationId, state, pending)
  return batch ? hashKey([state.reconcileSequence, state.terminalSequence, batch.key]) : null
}

export function verifiedChildInterrupts(
  conversationId: string,
  wire: WireChatSnapshot,
  pending: readonly AgUiInterrupt[],
): ChildInterruptForm[] {
  return verifiedApprovalBatch(conversationId, wire, pending)?.childForms ?? []
}

export function currentChildInterrupts(
  conversationId: string,
  state: AgUiChatSessionState,
  pending: readonly AgUiInterrupt[],
): ChildInterruptForm[] {
  return currentApprovalBatch(conversationId, state, pending)?.childForms ?? []
}
