import { describe, expect, it } from 'vitest'

import messages from '../../../messages/ko.json'
import { ApiError } from '@/lib/api/errors'
import {
  delegationsToConfirm,
  personalAccountUses,
  shareErrorMessage,
  transferBlockers,
} from '@/components/sharing/share-error'
import { canShareResource, isSharedWithMe } from '@/components/sharing/share-permissions'
import { shareLevelsFor } from '@/components/sharing/share-backend'
import { levelLabelKey } from '@/components/sharing/share-labels'

type Tree = { [key: string]: string | Tree }

function lookup(key: string): string {
  let node: string | Tree = messages.sharing as Tree
  for (const part of key.split('.')) {
    if (typeof node === 'string' || !(part in node)) throw new Error(`missing sharing.${key}`)
    node = node[part]
  }
  if (typeof node !== 'string') throw new Error(`not a leaf: sharing.${key}`)
  return node
}
const t = Object.assign((key: string) => lookup(key), {}) as unknown as Parameters<
  typeof shareErrorMessage
>[1]

describe('shareErrorMessage', () => {
  it.each([
    [new ApiError(422, 'grant_invalid', 'x', { reason: 'owner' }), 'errors.grantInvalid.owner'],
    [new ApiError(422, 'grant_invalid', 'x', { reason: 'mystery' }), 'errors.grantInvalid.default'],
    [new ApiError(403, 'permission_required', 'x'), 'errors.permission_required'],
    [new ApiError(403, 'capability_required', 'x'), 'errors.capability_required'],
    [new ApiError(404, 'not_found', 'x'), 'errors.not_found'],
    [new ApiError(404, 'SKILL_NOT_FOUND', 'x'), 'errors.not_found'],
    [new ApiError(409, 'transfer_blocked', 'x', {}), 'errors.transferBlocked'],
    [new ApiError(409, 'transfer_name_conflict', 'x'), 'errors.transfer_name_conflict'],
    [new ApiError(503, 'authz_unavailable', 'x'), 'errors.authz_unavailable'],
    [new ApiError(409, 'personal_account_shared', 'x', {}), 'errors.personal_account_shared'],
    [new Error('boom'), 'errors.generic'],
  ])('maps %o', (error, key) => {
    expect(shareErrorMessage(error, t)).toBe(lookup(key))
  })
})

describe('error details helpers', () => {
  it('reads confirm_required delegations defensively', () => {
    const error = new ApiError(409, 'confirm_required', 'x', {
      delegations: [
        { resource_type: 'tool', resource_id: 't1', name: 'Search' },
        { resource_type: 'credential', resource_id: 'c1' },
        'junk',
      ],
    })
    expect(delegationsToConfirm(error)).toEqual([
      { resource_type: 'tool', resource_id: 't1', name: 'Search' },
      { resource_type: 'credential', resource_id: 'c1', name: 'c1' },
    ])
    expect(delegationsToConfirm(new ApiError(409, 'conflict', 'x'))).toBeNull()
  })

  it('reads personal_account_shared carriers defensively', () => {
    const error = new ApiError(409, 'personal_account_shared', 'x', {
      personal_accounts: [
        { resource_type: 'agent', resource_id: 'a1', definition_key: 'srt_account' },
        { resource_type: 'tool' },
        null,
      ],
    })
    expect(personalAccountUses(error)).toEqual([
      { key: 'agent:a1', resourceType: 'agent', definitionKey: 'srt_account' },
    ])
    expect(personalAccountUses(new ApiError(409, 'conflict', 'x'))).toEqual([])
  })

  it('counts transfer_blocked dependents', () => {
    const error = new ApiError(409, 'transfer_blocked', 'x', {
      personal_account: 'srt_account',
      triggers: ['a', 'b'],
      api_keys: [],
    })
    expect(transferBlockers(error)).toEqual([
      { kind: 'personal_account', count: 1 },
      { kind: 'triggers', count: 2 },
    ])
  })
})

describe('share permissions and levels', () => {
  it('prefers my_permissions, then the owner, then the legacy own-list default', () => {
    expect(
      canShareResource({ myPermissions: ['can_run'], ownerId: 'me', currentUserId: 'me' }),
    ).toBe(false)
    expect(
      canShareResource({ myPermissions: ['can_share'], ownerId: 'x', currentUserId: 'me' }),
    ).toBe(true)
    expect(canShareResource({ ownerId: 'me', currentUserId: 'me' })).toBe(true)
    expect(canShareResource({ ownerId: 'x', currentUserId: 'me' })).toBe(false)
    expect(canShareResource({ ownerId: null, currentUserId: 'me' })).toBe(false)
    expect(canShareResource({ currentUserId: null })).toBe(true)
    expect(isSharedWithMe('x', 'me')).toBe(true)
    expect(isSharedWithMe('me', 'me')).toBe(false)
    expect(isSharedWithMe(null, 'me')).toBe(false)
  })

  it('follows the 10.1 level table and labels every level', () => {
    expect(shareLevelsFor('credential')).toEqual(['consumer', 'manager'])
    for (const type of ['agent', 'skill', 'tool', 'mcp_server'] as const) {
      expect(shareLevelsFor(type)).toEqual(['consumer', 'viewer', 'editor', 'manager'])
    }
    expect(lookup(levelLabelKey('agent', 'consumer'))).toBe('실행')
    expect(lookup(levelLabelKey('skill', 'consumer'))).toBe('사용')
    for (const level of ['viewer', 'editor', 'manager', 'owner', 'governor', 'install']) {
      expect(() => lookup(levelLabelKey('tool', level))).not.toThrow()
    }
  })
})
