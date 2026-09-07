import type { AppendMessage, ExternalThreadQueueAdapter } from '@assistant-ui/react'

import type {
  ConversationRunInput,
  ConversationRunInputEdit,
  ConversationRunInputList,
  ConversationRunInputRevision,
} from '@/lib/api/conversation-run-inputs'

export type { ConversationRunInput }

export type QueueRunStartAcceptance = {
  readonly inputId: string
  readonly inputStatus: 'pending' | 'claimed'
  readonly revision: number
  readonly position: number
  readonly runId?: string
}

export interface ServerMessageQueueApi {
  list(conversationId: string): Promise<ConversationRunInputList>
  edit(
    conversationId: string,
    inputId: string,
    request: ConversationRunInputEdit,
  ): Promise<ConversationRunInput>
  remove(
    conversationId: string,
    inputId: string,
    expectedRevision: number,
  ): Promise<ConversationRunInput>
  reorder(
    conversationId: string,
    items: readonly ConversationRunInputRevision[],
  ): Promise<readonly ConversationRunInput[]>
  promote(
    conversationId: string,
    inputId: string,
    expectedRevision: number,
  ): Promise<ConversationRunInput>
  resume(conversationId: string): Promise<ConversationRunInputList>
}

export type QueueOperationState =
  | { readonly kind: 'idle' }
  | { readonly kind: 'sending'; readonly requestId: string }
  | { readonly kind: 'reconciling'; readonly requestId: string }
  | { readonly kind: 'queued'; readonly requestId: string; readonly inputId: string }
  | {
      readonly kind: 'applied'
      readonly requestId: string
      readonly inputId: string
      readonly runId: string
    }
  | { readonly kind: 'failed'; readonly requestId?: string; readonly message: string }

export type ServerMessageQueueSnapshot = {
  readonly queuePaused: boolean
  readonly items: readonly ConversationRunInput[]
  readonly lastOperation: QueueOperationState
  readonly reconciliationError: string | null
  readonly rejectedSubmission: {
    readonly message: AppendMessage
    readonly strategy: 'enqueue' | 'interrupt'
  } | null
}

export interface ServerMessageQueueController {
  readonly adapter: ExternalThreadQueueAdapter
  updateCallbacks(
    callbacks: Pick<ServerMessageQueueOptions, 'submit' | 'createRequestId' | 'onClaimedRun'>,
  ): void
  enqueue(message: AppendMessage): Promise<void>
  steer(message: AppendMessage): Promise<void>
  edit(inputId: string, message: AppendMessage): Promise<void>
  move(inputId: string, placement: Parameters<ExternalThreadQueueAdapter['move']>[1]): Promise<void>
  remove(inputId: string): Promise<void>
  refresh(): Promise<void>
  reconcileRequest(requestId: string): Promise<ConversationRunInput | null>
  resume(): Promise<void>
  dismissRejectedSubmission(): void
  getSnapshot(): ServerMessageQueueSnapshot
  subscribe(listener: () => void): () => void
}

export type ServerMessageQueueOptions = {
  readonly conversationId: string
  readonly api: ServerMessageQueueApi
  readonly submit: (
    message: AppendMessage,
    options: { readonly strategy: 'enqueue' | 'interrupt'; readonly requestId: string },
  ) => Promise<QueueRunStartAcceptance>
  readonly createRequestId: () => string
  readonly onClaimedRun: (runId: string) => void
}
