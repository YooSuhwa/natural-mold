'use client'

import { createContext, useContext } from 'react'
import type { User } from '@/lib/types/user'
import type { DeepAgentsStateSnapshot } from '@/lib/chat/langgraph-runtime/deepagents-state'
import type { RunActivity } from '@/lib/chat/langgraph-runtime/activity-model'

export interface AssistantThreadDynamicContextValue {
  readonly activities: readonly RunActivity[]
  readonly agentImagePublicAsset: boolean
  readonly agentImageUrl?: string | null
  readonly agentName?: string
  readonly builderAgentSubtitle?: string
  readonly deepAgentsState?: DeepAgentsStateSnapshot
  readonly isBuilder: boolean
  readonly showMessageTimestamp: boolean
  readonly user?: User | null
}

export const AssistantThreadDynamicContext =
  createContext<AssistantThreadDynamicContextValue | null>(null)

export function useAssistantThreadDynamicContext(): AssistantThreadDynamicContextValue {
  const value = useContext(AssistantThreadDynamicContext)
  if (!value) {
    throw new Error('AssistantThreadDynamicContext is missing')
  }
  return value
}
