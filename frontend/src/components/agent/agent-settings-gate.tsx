'use client'

import type { ReactNode } from 'react'

import { AgentAccessProvider, agentAccessOf } from '@/components/agent/agent-access-context'
import { ApiErrorState } from '@/components/shared/api-error-state'
import { Skeleton } from '@/components/ui/skeleton'
import { ApiError } from '@/lib/api/client'
import { agentCan } from '@/lib/authz/permissions'
import { useAgent } from '@/lib/hooks/use-agents'
import type { RequestLevel } from '@/lib/types/access-request'

const SETTINGS_LEVELS: readonly RequestLevel[] = ['viewer', 'editor']
// A runner reaches the agent but not its configuration: the same 8.4 403 the
// settings routes answer, so the H-01 request button is offered.
const CONFIG_REQUIRED = new ApiError(403, 'permission_required', 'can_view_config', {
  required: 'can_view_config',
  request_access: true,
})

/**
 * Route gate of `/agents/[id]/settings` and `/visual-settings` (plan 13.9 row 2): without
 * `can_view_config` the whole route becomes the permission notice; otherwise the screen
 * gets the caller's access (read-only without `can_edit`).
 */
export function AgentSettingsGate({ agentId, children }: { agentId: string; children: ReactNode }) {
  const { data: agent, isLoading, error, refetch } = useAgent(agentId)

  if (isLoading) {
    return (
      <div className="flex flex-1 flex-col gap-4 p-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }
  const denied = error ?? (agent && !agentCan(agent, 'can_view_config') ? CONFIG_REQUIRED : null)
  if (denied) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <ApiErrorState
          error={denied}
          resource={{ type: 'agent', id: agentId, name: agent?.name, levels: SETTINGS_LEVELS }}
          onRetry={() => void refetch()}
        />
      </div>
    )
  }
  return <AgentAccessProvider access={agentAccessOf(agent)}>{children}</AgentAccessProvider>
}
