import { describe, expect, it } from 'vitest'

import type { Skill } from '../types/skill'
import { filterSkillList, skillMatchesStateFilter } from '../skill-state-filters'

function buildSkill(overrides: Partial<Skill>): Skill {
  return {
    id: 'skill-1',
    name: 'Weather Skill',
    slug: 'weather-skill',
    description: '날씨를 요약합니다.',
    kind: 'package',
    version: '0.1.0',
    storage_path: null,
    content_hash: 'hash-1',
    size_bytes: 1024,
    used_by_count: 0,
    package_metadata: null,
    credential_requirements: null,
    execution_profile: null,
    current_revision_id: null,
    latest_evaluation_summary: null,
    health: null,
    last_modified_at: '2026-06-01T00:00:00Z',
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    origin_summary: null,
    publication_summary: null,
    installation: null,
    ...overrides,
  }
}

describe('skill state filters', () => {
  it('treats stale evaluation summaries as rerun-needed skills', () => {
    const skill = buildSkill({
      latest_evaluation_summary: {
        status: 'stale',
        latest_run_id: 'run-1',
        evaluation_set_id: 'set-1',
        pass_rate: 0.9,
        skill_content_hash: 'old-hash',
        created_at: '2026-06-01T00:00:00Z',
        completed_at: '2026-06-01T00:01:00Z',
      },
    })

    expect(skillMatchesStateFilter(skill, 'needs_rerun')).toBe(true)
  })

  it('keeps publication filters independent from quality health filters', () => {
    const publishedSkill = buildSkill({
      id: 'published',
      publication_summary: {
        state: 'published_private',
        item_id: 'item-1',
        visibility: 'private',
        status: 'published',
        is_listed: false,
        latest_version_id: 'version-1',
        version_number: 1,
        shared_user_count: 0,
      },
    })
    const localSkill = buildSkill({
      id: 'local',
      publication_summary: {
        state: 'not_published',
        is_listed: false,
        shared_user_count: 0,
      },
    })

    expect(skillMatchesStateFilter(publishedSkill, 'published')).toBe(true)
    expect(skillMatchesStateFilter(localSkill, 'local_draft')).toBe(true)
    expect(skillMatchesStateFilter(publishedSkill, 'local_draft')).toBe(false)
  })

  it('combines kind, state, and search filters', () => {
    const credentialsSkill = buildSkill({
      id: 'credentials',
      name: 'Credential Weather',
      health: {
        state: 'needs_credentials',
        label: '자격증명 필요',
        reason: '필수 연결이 없습니다.',
        severity: 'warning',
      },
    })
    const textSkill = buildSkill({
      id: 'text',
      name: 'Credential Notes',
      kind: 'text',
      health: {
        state: 'needs_credentials',
        label: '자격증명 필요',
        reason: '필수 연결이 없습니다.',
        severity: 'warning',
      },
    })

    expect(
      filterSkillList([credentialsSkill, textSkill], {
        kind: 'package',
        state: 'needs_credentials',
        query: 'weather',
      }).map((skill) => skill.id),
    ).toEqual(['credentials'])
  })
})
