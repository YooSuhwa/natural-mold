import {
  canStart,
  pendingConnections,
  readinessItemAction,
} from '@/components/readiness/readiness-actions'
import type { ReadinessItem } from '@/lib/types/readiness'

const perUser: ReadinessItem = {
  kind: 'mcp_server',
  id: 'srv-1',
  name: 'Slack',
  required: true,
  result: 'connect_by_me',
  credential: { mode: 'per_user', status: 'missing', definition_key: 'slack_oauth2' },
  action: { type: 'oauth_start' },
}

describe('readinessItemAction (S21 buttons)', () => {
  it('opens the per-user connection dialog for my missing per-user connection', () => {
    expect(readinessItemAction(perUser, 'agent-1')).toEqual({
      type: 'bind',
      resourceType: 'mcp_server',
      resourceId: 'srv-1',
      resourceName: 'Slack',
      definitionKey: 'slack_oauth2',
    })
  })

  it('sends my expired shared credential to the credentials screen', () => {
    const item: ReadinessItem = {
      ...perUser,
      kind: 'tool',
      credential: { mode: 'shared', status: 'expired', credential_id: 'c-1' },
    }
    expect(readinessItemAction(item, 'agent-1')).toEqual({ type: 'credentials' })
  })

  it('notifies the owner and requests access to a named item or the agent', () => {
    const owner: ReadinessItem = {
      kind: 'tool',
      name: 'ERP',
      required: false,
      result: 'owner_must_connect',
    }
    expect(readinessItemAction(owner, 'agent-1')).toEqual({ type: 'notify' })
    const denied: ReadinessItem = {
      kind: 'sub_agent',
      id: 'child',
      name: '정산',
      required: true,
      result: 'no_permission',
      action: { type: 'request_access', via: 'agent_manager' },
    }
    expect(readinessItemAction(denied, 'agent-1')).toMatchObject({
      type: 'request',
      resourceType: 'agent',
      resourceId: 'child',
    })
    const counted: ReadinessItem = {
      ...denied,
      kind: 'tool',
      id: undefined,
      name: undefined,
      count: 2,
    }
    expect(readinessItemAction(counted, 'agent-1')).toEqual({
      type: 'request',
      resourceType: 'agent',
      resourceId: 'agent-1',
      resourceName: null,
    })
  })

  it('offers nothing while an administrator must check', () => {
    const item: ReadinessItem = { kind: 'llm_model', required: true, result: 'unavailable' }
    expect(readinessItemAction(item, 'agent-1')).toBeNull()
  })

  it('starts only when every required item is ready and counts my pending connections', () => {
    expect([
      canStart('ready'),
      canStart('limited'),
      canStart('needs_connection'),
      canStart('blocked'),
    ]).toEqual([true, true, false, false])
    expect(pendingConnections([perUser, { ...perUser, required: false }])).toBe(1)
  })
})
