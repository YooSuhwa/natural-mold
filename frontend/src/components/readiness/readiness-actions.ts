import type { RequestableType } from '@/lib/types/access-request'
import type { ReadinessItem, ReadinessKind, ReadinessOverall } from '@/lib/types/readiness'

export type ReadinessItemAction =
  | {
      readonly type: 'bind'
      readonly resourceType: 'tool' | 'mcp_server'
      readonly resourceId: string
      readonly resourceName: string
      readonly definitionKey: string | null
    }
  | { readonly type: 'credentials' }
  | { readonly type: 'notify' }
  | {
      readonly type: 'request'
      readonly resourceType: RequestableType
      readonly resourceId: string
      readonly resourceName: string | null
    }

const REQUESTABLE: Partial<Record<ReadinessKind, RequestableType>> = {
  tool: 'tool',
  skill: 'skill',
  mcp_server: 'mcp_server',
  sub_agent: 'agent',
}

/**
 * The S21 button of one 12.1 item: `connect_by_me` on a per-user tool/MCP server opens
 * the S23 dialog (anything else of mine — an expired shared credential or the model's —
 * is fixed on the credentials screen); `owner_must_connect` notifies the owner;
 * `no_permission` requests access to the item, or to the agent when it is counted only
 * (`via: agent_manager`). `unavailable` waits for an administrator.
 */
export function readinessItemAction(
  item: ReadinessItem,
  agentId: string,
): ReadinessItemAction | null {
  if (item.result === 'connect_by_me') {
    const bindable = item.kind === 'tool' || item.kind === 'mcp_server'
    if (bindable && item.id && item.credential?.mode === 'per_user') {
      return {
        type: 'bind',
        resourceType: item.kind === 'tool' ? 'tool' : 'mcp_server',
        resourceId: item.id,
        resourceName: item.name ?? '',
        definitionKey: item.credential.definition_key ?? null,
      }
    }
    return { type: 'credentials' }
  }
  if (item.result === 'owner_must_connect') return { type: 'notify' }
  if (item.result === 'no_permission' && item.action?.type === 'request_access') {
    const requestable = REQUESTABLE[item.kind]
    if (item.id && requestable) {
      return {
        type: 'request',
        resourceType: requestable,
        resourceId: item.id,
        resourceName: item.name ?? null,
      }
    }
    return { type: 'request', resourceType: 'agent', resourceId: agentId, resourceName: null }
  }
  return null
}

/** The panel's "확인" (plan "연결하고 시작") is active once every required item is ready (12.2 ready/limited). */
export function canStart(overall: ReadinessOverall): boolean {
  return overall === 'ready' || overall === 'limited'
}

/** Required items still to connect by me (the panel summary count). */
export function pendingConnections(items: readonly ReadinessItem[]): number {
  return items.filter((item) => item.required && item.result === 'connect_by_me').length
}
