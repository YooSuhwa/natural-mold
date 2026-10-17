'use client'

import { createContext, useContext, type ReactNode } from 'react'

import { agentCan } from '@/lib/authz/permissions'

/** What the caller may do on the agent a settings screen edits (plan 13.9 row 2). */
export interface AgentAccess {
  canEdit: boolean
  canShare: boolean
  canDeploy: boolean
  canDelete: boolean
  canRun: boolean
}

const FULL_ACCESS: AgentAccess = {
  canEdit: true,
  canShare: true,
  canDeploy: true,
  canDelete: true,
  canRun: true,
}

const AgentAccessContext = createContext<AgentAccess>(FULL_ACCESS)

export function agentAccessOf(
  agent: { my_permissions?: readonly string[] | null; view?: string } | null | undefined,
): AgentAccess {
  return {
    canEdit: agentCan(agent, 'can_edit'),
    canShare: agentCan(agent, 'can_share'),
    canDeploy: agentCan(agent, 'can_deploy'),
    canDelete: agentCan(agent, 'can_delete'),
    canRun: agentCan(agent, 'can_run'),
  }
}

export function AgentAccessProvider({
  access,
  children,
}: {
  access: AgentAccess
  children: ReactNode
}) {
  return <AgentAccessContext.Provider value={access}>{children}</AgentAccessContext.Provider>
}

/** Full access outside a provider (creation screens edit a draft the caller owns). */
export function useAgentAccess(): AgentAccess {
  return useContext(AgentAccessContext)
}
