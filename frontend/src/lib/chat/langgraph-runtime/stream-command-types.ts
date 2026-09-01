import type { ThreadMessage } from '@assistant-ui/react'
import type { BaseMessage } from '@langchain/core/messages'
import type { UseStreamReturn } from '@langchain/react'
import type { PendingEditRenderState, PendingReloadRenderState } from './stream-edit-reload-types'
import type { ConvertedMessage, VisibleMessageWithId } from './stream-message-types'
import type { PendingNewSubmitState } from './use-submit-checkpoint-controller'
import type { ThreadRunNotice } from './stream-thread-state-projection'

export interface SubmitCheckpointActions {
  readonly pendingSubmit: PendingNewSubmitState | null
  readonly beginPendingSubmit: (content: string, baseMessageCount: number) => PendingNewSubmitState
  readonly clearPendingSubmit: (content: string, attemptId: number) => boolean
  readonly clearConversationPendingSubmit: () => void
}

export interface ReconciliationActions {
  readonly lifetime: object
  readonly lifetimeRef: { readonly current: object }
  readonly getPendingEditAttemptId: () => number | null
  readonly getPendingReloadAttemptId: () => number | null
  readonly setThreadRunNotice: (notice: ThreadRunNotice | null) => void
  readonly clearServerHydrationState: () => void
  readonly beginPendingEdit: (value: PendingEditRenderState) => number
  readonly updatePendingEdit: (
    conversationId: string,
    attemptId: number,
    value: PendingEditRenderState,
  ) => void
  readonly clearPendingEdit: (conversationId: string, attemptId?: number) => void
  readonly beginPendingReload: (value: PendingReloadRenderState) => number
  readonly clearPendingReload: (conversationId: string, attemptId?: number) => void
  readonly cancelPostRunHydration: () => void
  readonly getLatestVisibleMessages: () => readonly BaseMessage[]
  readonly stagePendingEditBase: (
    messages: readonly BaseMessage[],
    convertedMessages: readonly ConvertedMessage[],
  ) => void
  readonly getPendingEditBase: () => {
    readonly messages: readonly BaseMessage[] | null
    readonly convertedMessages: readonly ConvertedMessage[] | null
  }
}

export interface UseStreamCommandControllerOptions<StateType extends object> {
  readonly conversationId: string
  readonly stream: UseStreamReturn<StateType>
  readonly visibleMessages: readonly (Pick<ThreadMessage, 'id'> & VisibleMessageWithId)[]
  readonly langChainMessages: readonly BaseMessage[]
  readonly convertedMessages: readonly ConvertedMessage[]
  readonly onBeforeSubmit?: () => void
  readonly setChatCancelInFlight: (inFlight: boolean) => void
  readonly clearBranchPickerSuppression: () => void
  readonly submitCheckpoint: SubmitCheckpointActions
  readonly reconciliation: ReconciliationActions
}
