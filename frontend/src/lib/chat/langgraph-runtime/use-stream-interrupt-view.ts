'use client'

import { useCallback, useMemo, useState } from 'react'
import type { BaseMessage } from '@langchain/core/messages'
import type { UseStreamReturn } from '@langchain/react'
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
import { threadInterruptsFromStream } from './stream-thread-state-projection'

interface ResolvedInterruptsSnapshot {
  readonly conversationId: string
  readonly items: readonly ResolvedInterruptToolCall[]
}

const EMPTY_RESOLVED_INTERRUPTS: readonly ResolvedInterruptToolCall[] = []

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
  const updateResolved = useCallback(
    (
      updater: (
        current: readonly ResolvedInterruptToolCall[],
      ) => readonly ResolvedInterruptToolCall[],
    ) => {
      setResolvedState((current) => ({
        conversationId,
        items: updater(current?.conversationId === conversationId ? current.items : []),
      }))
    },
    [conversationId],
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
    () =>
      dedupeLangChainMessagesById(
        appendResolvedInterruptToolCallMessages(
          appendInterruptToolCallMessages(
            stripInterruptedRawToolCalls(messages, payloads, resolved),
            payloads,
          ),
          resolved,
        ),
      ),
    [messages, payloads, resolved],
  )
  return { payloads, payloadsById, allPayloadsById, messagesWithInterrupts, updateResolved }
}
