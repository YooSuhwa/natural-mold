import type { AppendMessage, ExternalThreadQueueAdapter } from '@assistant-ui/react'

import type {
  ConversationRunInput,
  ConversationRunInputList,
} from '@/lib/api/conversation-run-inputs'
import { editableInputFromMessage, queueItemFromInput } from './queue-message-projection'
import type {
  QueueOperationState,
  ServerMessageQueueController,
  ServerMessageQueueOptions,
  ServerMessageQueueSnapshot,
} from './server-message-queue-contract'
import { createClaimedRunTracker } from './server-message-queue-claimed-runs'
import {
  errorMessage,
  reconciledQueueOperation,
  reorderedInputs,
  replaceInput,
} from './server-message-queue-mutations'

export type {
  QueueRunStartAcceptance,
  ServerMessageQueueApi,
  ServerMessageQueueController,
} from './server-message-queue-contract'

export function createServerMessageQueue(
  options: ServerMessageQueueOptions,
): ServerMessageQueueController {
  let serverInputs: readonly ConversationRunInput[] = []
  let queuePaused = false
  let lastOperation: QueueOperationState = { kind: 'idle' }
  let reconciliationError: string | null = null
  let rejectedSubmission: ServerMessageQueueSnapshot['rejectedSubmission'] = null
  let snapshot: ServerMessageQueueSnapshot = {
    queuePaused,
    items: serverInputs,
    lastOperation,
    reconciliationError,
    rejectedSubmission,
  }
  const claimedRuns = createClaimedRunTracker(options.onClaimedRun)
  const listeners = new Set<() => void>()

  const emit = (): void => {
    snapshot = {
      queuePaused,
      items: serverInputs,
      lastOperation,
      reconciliationError,
      rejectedSubmission,
    }
    for (const listener of listeners) listener()
  }
  const setOperation = (operation: QueueOperationState): void => {
    lastOperation = operation
    emit()
  }
  const failUnknownInput = (inputId: string): void => {
    setOperation({ kind: 'failed', requestId: inputId, message: `Unknown queue item ${inputId}` })
  }
  const applyInputs = (inputs: readonly ConversationRunInput[]): void => {
    serverInputs = inputs
    const pending = inputs.filter((input) => input.status === 'pending')
    adapter.items = pending.filter((input) => input.priority < 100).map(queueItemFromInput)
    adapter.steerItems = pending.filter((input) => input.priority >= 100).map(queueItemFromInput)
    lastOperation = claimedRuns.observeInputs(inputs, lastOperation)
    emit()
  }
  const applyList = (list: ConversationRunInputList): void => {
    queuePaused = list.queue_paused
    reconciliationError = null
    if (lastOperation.kind === 'reconciling') {
      const reconciled = reconciledQueueOperation(lastOperation, list)
      if (reconciled.accepted) rejectedSubmission = null
      lastOperation = reconciled.operation
    }
    applyInputs(list.items)
  }
  const submit = async (
    message: AppendMessage,
    strategy: 'enqueue' | 'interrupt',
  ): Promise<void> => {
    const requestId = options.createRequestId()
    setOperation({ kind: 'sending', requestId })
    try {
      const accepted = await options.submit(message, { strategy, requestId })
      rejectedSubmission = null
      setOperation(claimedRuns.recordAccepted(accepted, requestId))
      try {
        applyList(await options.api.list(options.conversationId))
      } catch (error) {
        reconciliationError = errorMessage(error)
        emit()
      }
    } catch (error) {
      rejectedSubmission = { message, strategy }
      const failureMessage = errorMessage(error)
      lastOperation = { kind: 'reconciling', requestId }
      try {
        applyList(await options.api.list(options.conversationId))
        if (snapshot.lastOperation.kind === 'failed') {
          lastOperation = { kind: 'failed', requestId, message: failureMessage }
          emit()
        }
      } catch (reconciliationFailure) {
        reconciliationError = errorMessage(reconciliationFailure)
        emit()
      }
    }
  }
  const mutate = async (
    operation: () => Promise<ConversationRunInput>,
    inputId?: string,
  ): Promise<void> => {
    try {
      applyInputs(replaceInput(serverInputs, await operation()))
    } catch (error) {
      setOperation({
        kind: 'failed',
        ...(inputId ? { requestId: inputId } : {}),
        message: errorMessage(error),
      })
    }
  }

  const adapter: ExternalThreadQueueAdapter = {
    items: [],
    steerItems: [],
    enqueue: (message) => void controller.enqueue(message),
    steer: (message) => void controller.steer(message),
    move: (inputId, placement) => void controller.move(inputId, placement),
    edit: (inputId, message) => void controller.edit(inputId, message),
    remove: (inputId) => void controller.remove(inputId),
  }

  const controller: ServerMessageQueueController = {
    adapter,
    enqueue: (message) => submit(message, 'enqueue'),
    steer: (message) => submit(message, 'interrupt'),
    edit: async (inputId, message) => {
      const input = serverInputs.find((item) => item.id === inputId && item.status === 'pending')
      if (!input) {
        failUnknownInput(inputId)
        return
      }
      await mutate(
        () =>
          options.api.edit(options.conversationId, inputId, {
            expectedRevision: input.revision,
            input: editableInputFromMessage(message, input.input_payload),
          }),
        inputId,
      )
    },
    move: async (inputId, placement) => {
      const pending = serverInputs.filter((input) => input.status === 'pending')
      const input = pending.find((item) => item.id === inputId)
      if (!input) {
        failUnknownInput(inputId)
        return
      }
      if (placement.lane === 'steer') {
        await mutate(
          () => options.api.promote(options.conversationId, inputId, input.revision),
          inputId,
        )
        return
      }
      try {
        const reordered = reorderedInputs(pending, inputId, placement)
        applyInputs([
          ...serverInputs.filter((item) => item.status !== 'pending'),
          ...(await options.api.reorder(
            options.conversationId,
            reordered.map((item) => ({ id: item.id, revision: item.revision })),
          )),
        ])
      } catch (error) {
        setOperation({ kind: 'failed', requestId: inputId, message: errorMessage(error) })
      }
    },
    remove: async (inputId) => {
      const input = serverInputs.find((item) => item.id === inputId && item.status === 'pending')
      if (!input) {
        failUnknownInput(inputId)
        return
      }
      await mutate(
        () => options.api.remove(options.conversationId, inputId, input.revision),
        inputId,
      )
    },
    refresh: async () => {
      try {
        applyList(await options.api.list(options.conversationId))
      } catch (error) {
        reconciliationError = errorMessage(error)
        emit()
      }
    },
    reconcileRequest: async (requestId) => {
      setOperation({ kind: 'reconciling', requestId })
      const list = await options.api.list(options.conversationId)
      applyList(list)
      return list.items.find((input) => input.client_request_id === requestId) ?? null
    },
    dismissRejectedSubmission: () => {
      rejectedSubmission = null
      if (lastOperation.kind === 'failed') lastOperation = { kind: 'idle' }
      emit()
    },
    resume: async () => {
      try {
        applyList(await options.api.resume(options.conversationId))
      } catch (error) {
        setOperation({ kind: 'failed', message: errorMessage(error) })
      }
    },
    getSnapshot: () => snapshot,
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
  return controller
}
