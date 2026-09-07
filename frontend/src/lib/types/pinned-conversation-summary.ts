export type PinnedSummarySourceStatus = 'current' | 'changed' | 'deleted' | 'other_branch'

export type PinnedConversationSummary = {
  readonly conversation_id: string
  readonly source_message_id: string
  readonly source_branch_checkpoint_id: string
  readonly snapshot_text: string
  readonly source_status: PinnedSummarySourceStatus
  readonly created_at: string
  readonly updated_at: string
}

export type PinnedConversationSummaryEnvelope = {
  readonly summary: PinnedConversationSummary | null
}
