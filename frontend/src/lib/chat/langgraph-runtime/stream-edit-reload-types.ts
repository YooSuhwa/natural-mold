import type { PendingCheckpointEditSubmit } from './use-checkpoint-fork-handlers'

export interface PendingEditRenderState extends PendingCheckpointEditSubmit {
  readonly conversationId: string
  readonly staleTailFingerprints: readonly string[]
  readonly staleTailContentFingerprints: readonly string[]
  readonly staleConvertedTailContentFingerprints: readonly string[]
  readonly requiresLatestBranchMetadata: boolean
  readonly pendingBranchTotal: number | null
}

export interface PendingReloadRenderState {
  readonly conversationId: string
  readonly parentId: string | null
  readonly targetId: string | null
  readonly targetIndex: number | null
  readonly promptMessageKey: string | null
  readonly staleMessageKey: string
  readonly requiredAssistantBranch: PendingReloadRequiredAssistantBranch
  readonly requiredUserBranch: PendingReloadRequiredUserBranch | null
}

export interface PendingReloadRequiredAssistantBranch {
  readonly index: number
  readonly branchTotal: number
}

export interface PendingReloadRequiredUserBranch {
  readonly id: string | null
  readonly index: number
  readonly branchTotal: number
}
