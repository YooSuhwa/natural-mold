import { describe, expect, it } from 'vitest'

import type { Skill } from '@/lib/types/skill'

import { getVisibleSkillDetailTabs } from '../skill-detail-tabs'

function buildSkill(overrides: Partial<Skill> = {}): Skill {
  return {
    id: 'skill-1',
    name: 'Korea Weather',
    slug: 'korea-weather',
    description: '한국 날씨를 정리합니다.',
    kind: 'package',
    version: '0.1.0',
    storage_path: null,
    content_hash: 'hash-current',
    size_bytes: 1024,
    used_by_count: 1,
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

describe('getVisibleSkillDetailTabs', () => {
  it('hides optional advanced tabs when the skill has no matching evidence', () => {
    expect(getVisibleSkillDetailTabs(buildSkill())).toEqual(['content', 'metadata'])
  })

  it('does not treat missing evaluation summaries as evaluation evidence', () => {
    expect(
      getVisibleSkillDetailTabs(
        buildSkill({
          latest_evaluation_summary: {
            status: 'missing',
            latest_run_id: null,
            evaluation_set_id: null,
            pass_rate: null,
            skill_content_hash: null,
            created_at: null,
            completed_at: null,
          },
          health: {
            state: 'needs_evaluation',
            label: '평가 없음',
            reason: 'No evaluation run exists.',
            severity: 'neutral',
          },
        }),
      ),
    ).toEqual(['content', 'metadata'])
  })

  it('shows evaluation when a generated evaluation set exists before the first run', () => {
    expect(
      getVisibleSkillDetailTabs(
        buildSkill({
          latest_evaluation_summary: {
            status: 'missing',
            latest_run_id: null,
            evaluation_set_id: 'set-1',
            pass_rate: null,
            skill_content_hash: null,
            created_at: null,
            completed_at: null,
          },
          health: {
            state: 'needs_evaluation',
            label: '평가 없음',
            reason: 'No evaluation run exists.',
            severity: 'neutral',
          },
        }),
      ),
    ).toContain('evaluation')
  })

  it('shows credentials when requirements exist or a credentials deep link is active', () => {
    expect(
      getVisibleSkillDetailTabs(
        buildSkill({
          credential_requirements: [
            {
              key: 'weather_key',
              definition_key: 'weather_api',
              required: true,
              label: 'Weather API',
              description: null,
              fields: ['api_key'],
              injection: 'env',
              scope: 'user',
            },
          ],
        }),
      ),
    ).toContain('credentials')

    expect(getVisibleSkillDetailTabs(buildSkill(), 'credentials')).toContain('credentials')
  })

  it('shows evaluation and history only when they have a signal or deep link', () => {
    expect(
      getVisibleSkillDetailTabs(
        buildSkill({
          latest_evaluation_summary: {
            status: 'completed',
            latest_run_id: 'run-1',
            evaluation_set_id: 'set-1',
            pass_rate: 0.92,
            skill_content_hash: 'hash-current',
            created_at: '2026-06-01T00:00:00Z',
            completed_at: '2026-06-01T00:01:00Z',
          },
          current_revision_id: 'revision-1',
        }),
      ),
    ).toEqual(['content', 'evaluation', 'history', 'metadata'])

    expect(getVisibleSkillDetailTabs(buildSkill(), 'evaluation')).toContain('evaluation')
    expect(getVisibleSkillDetailTabs(buildSkill(), 'history')).toContain('history')
    expect(
      getVisibleSkillDetailTabs(
        buildSkill({
          health: {
            state: 'needs_rerun',
            label: '재평가 필요',
            reason: 'Skill content changed after the latest completed evaluation.',
            severity: 'warning',
          },
        }),
      ),
    ).toContain('evaluation')
  })
})
