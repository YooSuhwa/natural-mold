import { isNotFoundError, mapApiErrorToUiState } from '@/lib/api/error-ui'
import { ApiError } from '@/lib/api/errors'

describe('mapApiErrorToUiState (plan 13.1)', () => {
  it.each([
    [new ApiError(401, 'unauthorized', 'x'), 'unauthenticated'],
    [new ApiError(403, 'permission_required', 'x'), 'permission_required'],
    [
      new ApiError(403, 'capability_required', 'x', { capability: 'cap_create_tool' }),
      'capability_required',
    ],
    [new ApiError(403, 'org_access_denied', 'x'), 'org_access_denied'],
    [new ApiError(404, 'AGENT_NOT_FOUND', 'x'), 'not_found'],
    [new ApiError(404, 'marketplace_item_not_found', 'x'), 'not_found'],
    [new ApiError(409, 'agent_not_ready', 'x', { agent_id: 'a-1' }), 'agent_not_ready'],
    [new ApiError(429, 'quota_exceeded', 'x'), 'quota_exceeded'],
    [new ApiError(429, 'rate_limited', 'x'), 'quota_exceeded'],
    [new ApiError(503, 'authz_unavailable', 'x'), 'authz_unavailable'],
    [new ApiError(500, 'internal', 'x'), 'unknown'],
    [new Error('plain'), 'unknown'],
  ])('maps %o to %s', (error, kind) => {
    expect(mapApiErrorToUiState(error).kind).toBe(kind)
  })

  it('reads the request flag, capability and agent from details', () => {
    const blocked = new ApiError(403, 'permission_required', 'x', { request_access: false })
    expect(mapApiErrorToUiState(blocked).requestable).toBe(false)
    expect(mapApiErrorToUiState(new ApiError(403, 'permission_required', 'x')).requestable).toBe(
      true,
    )
    const cap = new ApiError(403, 'capability_required', 'x', { capability: 'cap_create_skill' })
    expect(mapApiErrorToUiState(cap).capability).toBe('cap_create_skill')
    const notReady = new ApiError(409, 'agent_not_ready', 'x', { agent_id: 'a-1' })
    expect(mapApiErrorToUiState(notReady)).toMatchObject({ agentId: 'a-1', retryable: false })
    expect(mapApiErrorToUiState(new ApiError(503, 'authz_unavailable', 'x')).retryable).toBe(true)
  })

  it('treats only not-found codes as not found', () => {
    expect(isNotFoundError(new ApiError(404, 'SKILL_NOT_FOUND', 'x'))).toBe(true)
    expect(isNotFoundError(new ApiError(404, 'route_missing', 'x'))).toBe(false)
    expect(isNotFoundError(new ApiError(403, 'not_found', 'x'))).toBe(false)
  })
})
