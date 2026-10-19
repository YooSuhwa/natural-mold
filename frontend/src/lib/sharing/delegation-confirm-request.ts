import { agentsApi } from '@/lib/api/agents'
import { ApiError } from '@/lib/api/errors'
import type { Agent, AgentUpdateRequest } from '@/lib/types'
import type { DelegatedAccount } from '@/lib/types/grant'
import { delegationsToConfirm } from './delegations'

/**
 * D17 (E-06): saving the links of an agent that is already shared can lend new accounts
 * to its recipients; the backend answers `409 confirm_required`. The mounted
 * `DelegationConfirmHost` asks the user; without a host the save stays refused.
 */
export interface DelegationConfirmRequest {
  delegations: DelegatedAccount[]
  resolve: (confirmed: boolean) => void
}

type Listener = (request: DelegationConfirmRequest) => void
let listener: Listener | null = null

export function subscribeDelegationConfirm(next: Listener): () => void {
  listener = next
  return () => {
    if (listener === next) listener = null
  }
}

export function requestDelegationConfirm(delegations: DelegatedAccount[]): Promise<boolean> {
  const current = listener
  if (!current) return Promise.resolve(false)
  return new Promise((resolve) => current({ delegations, resolve }))
}

/** Thrown when the user declines the confirmation; the save did not happen. */
export class DelegationDeclinedError extends Error {
  constructor() {
    super('delegation confirmation declined')
    this.name = 'DelegationDeclinedError'
  }
}

/** `PUT /api/agents/{id}`, resent with `confirm_delegation` once the user confirms. */
export async function updateAgentConfirmingDelegation(
  id: string,
  data: AgentUpdateRequest,
): Promise<Agent> {
  try {
    return await agentsApi.update(id, data)
  } catch (error) {
    const delegations = delegationsToConfirm(error)
    if (!(error instanceof ApiError) || delegations === null) throw error
    if (!(await requestDelegationConfirm(delegations))) throw new DelegationDeclinedError()
    const confirmed: AgentUpdateRequest & { confirm_delegation: boolean } = {
      ...data,
      confirm_delegation: true,
    }
    return agentsApi.update(id, confirmed)
  }
}
