'use client'

import { useCallback, useMemo, useState } from 'react'
import type { BaseMessage } from '@langchain/core/messages'
import type { UseStreamReturn } from '@langchain/react'
import type { StandardInterruptPayload } from '@/lib/types'
import {
  activeInterruptPayloads,
  appendInterruptToolCallMessages,
  appendResolvedInterruptToolCallMessages,
  standardPayloadsFromInterrupts,
  stripInterruptedRawToolCalls,
  type LangGraphInterruptLike,
  type ResolvedInterruptToolCall,
} from './hitl-interrupts'
import { dedupeLangChainMessagesById, stableString } from './message-list'
import { isHumanMessage } from './stream-message-content'
import { threadInterruptsFromStream } from './stream-thread-state-projection'

interface ResolvedInterruptsSnapshot {
  readonly conversationId: string
  readonly items: readonly ResolvedInterruptToolCall[]
}

const EMPTY_RESOLVED_INTERRUPTS: readonly ResolvedInterruptToolCall[] = []

function projectInterruptMessages(
  messages: readonly BaseMessage[],
  pending: readonly StandardInterruptPayload[],
  resolved: readonly ResolvedInterruptToolCall[],
): BaseMessage[] {
  if (resolved.length === 0) {
    return appendInterruptToolCallMessages(
      stripInterruptedRawToolCalls(messages, pending, []),
      pending,
    )
  }
  const turns: BaseMessage[][] = [[]]
  for (const message of messages) {
    if (isHumanMessage(message) && turns[turns.length - 1].length) turns.push([])
    turns[turns.length - 1].push(message)
  }
  return turns.flatMap((turn, index) => {
    const latest = index === turns.length - 1
    const userMessageId = turn[0] && isHumanMessage(turn[0]) ? turn[0].id : undefined
    const decisions = resolved.filter((item) =>
      item.userMessageId ? item.userMessageId === userMessageId : latest,
    )
    const approvals = latest ? pending : []
    if (!approvals.length && !decisions.length) return turn
    const history = stripInterruptedRawToolCalls(turn, approvals, decisions)
    // A subsequent approval may follow an accepted action in the same turn. Its
    // requires-action state must be the last assistant state consumed by the SDK.
    return appendInterruptToolCallMessages(
      appendResolvedInterruptToolCallMessages(history, decisions),
      approvals,
    )
  })
}

export function useStreamInterruptView<StateType extends object>({
  conversationId,
  stream,
  serverInterrupts,
  messages,
}: {
  readonly conversationId: string
  readonly stream: UseStreamReturn<StateType>
  readonly serverInterrupts: readonly LangGraphInterruptLike[]
  readonly messages: readonly BaseMessage[]
}) {
  const [resolvedState, setResolvedState] = useState<ResolvedInterruptsSnapshot | null>(null)
  const resolved =
    resolvedState?.conversationId === conversationId
      ? resolvedState.items
      : EMPTY_RESOLVED_INTERRUPTS
  const userMessageId = messages.findLast(isHumanMessage)?.id
  const updateResolved = useCallback(
    (
      updater: (
        current: readonly ResolvedInterruptToolCall[],
      ) => readonly ResolvedInterruptToolCall[],
    ) => {
      setResolvedState((current) => {
        const previous = current?.conversationId === conversationId ? current.items : []
        const items = updater(previous).map((item) =>
          userMessageId && !previous.includes(item) ? { ...item, userMessageId } : item,
        )
        return { conversationId, items }
      })
    },
    [conversationId, userMessageId],
  )
  const raw = [...serverInterrupts, ...stream.interrupts, ...threadInterruptsFromStream(stream)]
  const fingerprint = stableString(raw)
  const allPayloads = useMemo(
    () => standardPayloadsFromInterrupts(raw),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [fingerprint],
  )
  const payloads = useMemo(
    () => activeInterruptPayloads(allPayloads, messages, resolved),
    [allPayloads, messages, resolved],
  )
  const payloadsById = useMemo(
    () => new Map(payloads.map((payload) => [payload.interrupt_id, payload])),
    [payloads],
  )
  const allPayloadsById = useMemo(
    () => new Map(allPayloads.map((payload) => [payload.interrupt_id, payload])),
    [allPayloads],
  )
  const messagesWithInterrupts = useMemo(
    () => dedupeLangChainMessagesById(projectInterruptMessages(messages, payloads, resolved)),
    [messages, payloads, resolved],
  )
  return { payloads, payloadsById, allPayloadsById, messagesWithInterrupts, updateResolved }
}
