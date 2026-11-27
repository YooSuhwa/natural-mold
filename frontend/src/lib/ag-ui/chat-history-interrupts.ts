import type { Interrupt } from '@ag-ui/core'
import type {
  ThreadAssistantMessage,
  ThreadAssistantMessagePart,
  ThreadMessage,
  ToolCallMessagePart,
} from '@assistant-ui/react'
import { hashKey } from '@tanstack/react-query'
import { z } from 'zod'
import { standardInterruptToToolCalls } from '@/lib/chat/standard-interrupt'
import { moldyAgUiInterruptPayloads } from './chat-actions-interrupts'
import type { AgUiHistoryChild } from './chat-history-children'

interface RestoreContext {
  readonly runId: string | null
  readonly headId: string | null
  readonly children: readonly AgUiHistoryChild[]
}

const waiting = { type: 'requires-action', reason: 'interrupt' } as const
const jsonArguments = z.record(z.string(), z.json())

function sdkMetadata(message: ThreadAssistantMessage): Record<string, unknown> {
  const value: unknown = message.metadata.custom.agui
  return value && typeof value === 'object' && !Array.isArray(value) ? { ...value } : {}
}

function pendingCards(
  message: ThreadAssistantMessage,
  slots: readonly Interrupt[],
): ThreadAssistantMessagePart[] {
  const content = [...message.content]
  const byId = new Map(slots.map((slot) => [slot.id, slot]))
  const payloads = moldyAgUiInterruptPayloads(slots)
  for (const payload of payloads) {
    const slot = byId.get(payload.interrupt_id)
    for (const [actionIndex, call] of standardInterruptToToolCalls(payload).entries()) {
      if (!call.id) continue
      const args = jsonArguments.safeParse(call.args)
      if (!args.success) continue
      let index = content.findIndex(
        (part) =>
          part.type === 'tool-call' &&
          part.result === undefined &&
          (part.toolCallId === call.id ||
            (payload.action_requests.length === 1 && part.toolCallId === slot?.toolCallId)),
      )
      // A native middleware approval can omit the call ID. Match only one
      // unanswered call with the same public action inside this message.
      if (index < 0 && !slot?.toolCallId) {
        const action = payload.action_requests[actionIndex]
        const key = hashKey([action.name, action.args])
        const matches = content.flatMap((part, offset) =>
          part.type === 'tool-call' &&
          part.result === undefined &&
          !part.messages?.length &&
          !part.args.hitl_interrupt_id &&
          hashKey([part.toolName, part.args]) === key
            ? [offset]
            : [],
        )
        if (matches.length === 1) index = matches[0]
      }
      const previous = index >= 0 ? content[index] : undefined
      const toolCallId = previous?.type === 'tool-call' ? previous.toolCallId : call.id
      const cardArgs = { ...args.data, approval_id: toolCallId }
      const next: ToolCallMessagePart = {
        type: 'tool-call',
        toolCallId,
        toolName: call.name,
        args: cardArgs,
        argsText: JSON.stringify(cardArgs),
        providerExecuted: true,
      }
      if (index < 0) content.push(next)
      else content[index] = next
    }
  }
  for (const slot of slots) {
    if (
      (slot.reason === 'moldy.workflow_wait' || slot.reason === 'moldy.input_required') &&
      slot.message &&
      !content.some((part) => part.type === 'text' && part.text === slot.message)
    ) {
      content.push({ type: 'text', text: slot.message })
    }
  }
  return content
}

function markWaiting(
  message: ThreadAssistantMessage,
  slots: readonly Interrupt[],
  content: readonly ThreadAssistantMessagePart[],
): ThreadAssistantMessage {
  const custom = message.metadata.custom
  return {
    ...message,
    status: waiting,
    content,
    metadata: {
      ...message.metadata,
      custom: { ...custom, agui: { ...sdkMetadata(message), interrupts: [...slots] } },
    },
  }
}

function restoreChildren(
  message: ThreadAssistantMessage,
  parentRunId: string | null,
  context: RestoreContext,
  byOwner: ReadonlyMap<string, Interrupt[]>,
): ThreadAssistantMessage {
  const content = message.content.map((part) => {
    if (part.type !== 'tool-call' || !part.messages?.length) return part
    const nested = part.messages.map((child) => {
      if (child.role !== 'assistant') return child
      const record = context.children.find((row) => row.subagentRunId === child.id)
      if (
        !record ||
        record.parentToolCallId !== part.toolCallId ||
        (record.parentSubagentRunId ?? null) !== parentRunId
      )
        return child
      if (
        parentRunId === null &&
        record.parentMessageId &&
        record.parentMessageId !== message.id &&
        record.parentMessageId !== sdkMetadata(message).messageId
      ) {
        return child
      }
      const restored = restoreChildren(child, child.id, context, byOwner)
      const pending = byOwner
        .get(child.id)
        ?.filter((slot) => !record.interruptIds?.length || record.interruptIds.includes(slot.id))
      return pending?.length && record.status === 'interrupted'
        ? markWaiting(restored, pending, pendingCards(restored, pending))
        : restored
    })
    return { ...part, messages: nested }
  })
  return { ...message, content }
}

function createPendingHead(
  messages: readonly ThreadMessage[],
  slots: readonly Interrupt[],
  context: RestoreContext,
  tip: ThreadMessage | undefined,
): ThreadAssistantMessage {
  const key = JSON.stringify([context.runId, context.headId, slots.map((slot) => slot.id).sort()])
  let id = `moldy-interrupt:${key}`
  const existing = new Set(messages.map((message) => message.id))
  while (existing.has(id)) id += '#'
  return {
    id,
    role: 'assistant',
    createdAt: tip?.createdAt ?? new Date(0),
    content: [],
    status: waiting,
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: { moldyInterruptParentId: context.headId },
    },
  }
}

/** Bind visible cards to their own execution while the SDK reads every pending ID from its head. */
export function restoreAgUiInterrupts(
  messages: readonly ThreadMessage[],
  interrupts: readonly Interrupt[],
  context: RestoreContext,
): ThreadMessage[] {
  if (!interrupts.length) return [...messages]
  const tip = messages.find((message) => message.id === context.headId)
  if (messages.length && !tip) return [...messages]

  const byOwner = new Map<string, Interrupt[]>()
  const rootSlots: Interrupt[] = []
  for (const slot of interrupts) {
    if (!slot.subagentRunId) rootSlots.push(slot)
    else {
      const owned = byOwner.get(slot.subagentRunId) ?? []
      owned.push(slot)
      byOwner.set(slot.subagentRunId, owned)
    }
  }

  const restored = messages.map((message) =>
    byOwner.size && message.role === 'assistant'
      ? restoreChildren(message, null, context, byOwner)
      : message,
  )
  const tipIndex = restored.findIndex((message) => message.id === context.headId)
  const current = restored[tipIndex]
  const head =
    current?.role === 'assistant' ? current : createPendingHead(restored, interrupts, context, tip)
  const waitingHead = markWaiting(head, interrupts, pendingCards(head, rootSlots))
  if (current?.role === 'assistant') restored[tipIndex] = waitingHead
  else restored.push(waitingHead)
  return restored
}
