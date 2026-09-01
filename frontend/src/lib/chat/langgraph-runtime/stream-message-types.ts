import type { useExternalMessageConverter } from '@assistant-ui/react'
import type { BaseMessage } from '@langchain/core/messages'

export type ConvertedMessage = ReturnType<typeof useExternalMessageConverter>[number]
export type VisibleMessageWithId = {
  readonly id: string
  readonly role?: string
  readonly sourceId?: string
}

export type SnapshotCloneableMessage = BaseMessage & {
  readonly additional_kwargs?: unknown
  readonly invalid_tool_calls?: unknown
  readonly response_metadata?: unknown
  readonly status?: unknown
  readonly tool_call_id?: unknown
  readonly tool_calls?: unknown
  readonly usage_metadata?: unknown
}
