import type { ExternalThreadQueueAdapter } from '@assistant-ui/react'

import type {
  ConversationRunInput,
  ConversationRunInputList,
} from '@/lib/api/conversation-run-inputs'
import type { QueueOperationState } from './server-message-queue-contract'

export class QueueStateError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'QueueStateError'
  }
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Queue operation failed'
}

export function reconciledQueueOperation(
  operation: Extract<QueueOperationState, { kind: 'reconciling' }>,
  list: ConversationRunInputList,
): { readonly operation: QueueOperationState; readonly accepted: boolean } {
  const accepted = list.items.find((input) => input.client_request_id === operation.requestId)
  if (!accepted) {
    return {
      operation: {
        kind: 'failed',
        requestId: operation.requestId,
        message: 'Queue submission was rejected',
      },
      accepted: false,
    }
  }
  return {
    operation: {
      kind: 'queued',
      requestId: operation.requestId,
      inputId: accepted.id,
    },
    accepted: true,
  }
}

export function replaceInput(
  inputs: readonly ConversationRunInput[],
  replacement: ConversationRunInput,
): readonly ConversationRunInput[] {
  const found = inputs.some((input) => input.id === replacement.id)
  if (!found) return [...inputs, replacement]
  return inputs.map((input) => (input.id === replacement.id ? replacement : input))
}

export function reorderedInputs(
  inputs: readonly ConversationRunInput[],
  inputId: string,
  placement: Parameters<ExternalThreadQueueAdapter['move']>[1],
): readonly ConversationRunInput[] {
  const moving = inputs.find((input) => input.id === inputId)
  if (!moving) throw new QueueStateError(`Unknown queue item ${inputId}`)
  const remaining = inputs.filter((input) => input.id !== inputId)
  const anchorId = placement.insertAfter ?? placement.insertBefore
  if (anchorId === null) {
    return placement.insertAfter === null ? [moving, ...remaining] : [...remaining, moving]
  }
  if (anchorId === undefined) return inputs
  const anchorIndex = remaining.findIndex((input) => input.id === anchorId)
  if (anchorIndex < 0) throw new QueueStateError(`Unknown queue anchor ${anchorId}`)
  const insertionIndex = placement.insertAfter !== undefined ? anchorIndex + 1 : anchorIndex
  return [...remaining.slice(0, insertionIndex), moving, ...remaining.slice(insertionIndex)]
}
