import {
  buildResolutions,
  classify,
  planRemoval,
  type RowChoice,
} from '@/features/organizations/components/member-removal/removal-plan'
import type { MemberExitPreview, OwnedResource } from '@/lib/types/member-exit'

function owned(overrides: Partial<OwnedResource> = {}): OwnedResource {
  return {
    resource_type: 'agent',
    resource_id: 'a1',
    name: 'Agent',
    archived: false,
    transferable: true,
    blocked_by: null,
    ...overrides,
  }
}

function preview(items: OwnedResource[], isLastAdmin = false): MemberExitPreview {
  return {
    user_id: 'u1',
    status: 'active',
    is_last_admin: isLastAdmin,
    owned: items,
    grants: [],
    group_ids: [],
    roles: [],
    trigger_ids: [],
    deployment_ids: [],
    api_key_ids: [],
  }
}

describe('member removal plan', () => {
  it('classifies archived, unsupported, blocked and personal rows', () => {
    expect(classify(owned({ archived: true })).kind).toBe('archived')
    expect(classify(owned({ resource_type: 'marketplace_item' })).kind).toBe('unsupported')
    expect(classify(owned({ blocked_by: { triggers: ['t1', 't2'] } }))).toMatchObject({
      kind: 'blocked',
      blockers: [{ kind: 'triggers', count: 2 }],
    })
    const personal = owned({
      resource_type: 'credential',
      transferable: false,
      blocked_by: { personal_account: 'gmail' },
    })
    expect(classify(personal)).toMatchObject({
      kind: 'actionable',
      canTransfer: false,
      canDelete: true,
    })
    // Skill bindings block the delete only; a shared credential can still move.
    expect(
      classify(owned({ resource_type: 'credential', blocked_by: { skill_bindings: ['s1'] } })),
    ).toMatchObject({ kind: 'actionable', canTransfer: true, canDelete: false })
    expect(
      classify({ ...personal, blocked_by: { personal_account: 'gmail', skill_bindings: ['s1'] } }),
    ).toMatchObject({ kind: 'blocked', blockers: [{ kind: 'skill_bindings', count: 1 }] })
  })

  it('needs an owner for every transfer and stays closed while blocked', () => {
    const plan = planRemoval(preview([owned(), owned({ resource_id: 'a2', archived: true })]))
    expect(buildResolutions(plan, new Map())).toBeNull()
    const choices = new Map<string, RowChoice>([
      ['agent:a1', { action: 'transfer', newOwner: { id: 'u2', displayName: 'Lee' } }],
    ])
    expect(buildResolutions(plan, choices)).toEqual([
      { resource_type: 'agent', resource_id: 'a1', action: 'transfer', new_owner_id: 'u2' },
    ])
    expect(buildResolutions(planRemoval(preview([], true)), new Map())).toBeNull()
    const blocked = planRemoval(preview([owned({ blocked_by: { deployments: ['d1'] } })]))
    expect(blocked.blocked).toBe(true)
    expect(buildResolutions(blocked, choices)).toBeNull()
  })

  it('never deletes a non-credential even if asked', () => {
    const plan = planRemoval(preview([owned()]))
    const choices = new Map<string, RowChoice>([['agent:a1', { action: 'delete', newOwner: null }]])
    expect(buildResolutions(plan, choices)).toBeNull()
  })
})
