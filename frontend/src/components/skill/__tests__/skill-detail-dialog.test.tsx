import { describe, expect, it, vi } from 'vitest'

import { render, screen } from '../../../../tests/test-utils'
import type { Skill } from '@/lib/types/skill'

import { SkillDetailDialog } from '../skill-detail-dialog'

const mockUseSkill = vi.fn()
const mockUseSkillEvaluationSets = vi.fn()
const mockUseSkillEvaluationRuns = vi.fn()

vi.mock('@/lib/hooks/use-skills', () => ({
  useSkill: (...args: readonly unknown[]) => mockUseSkill(...args),
}))

vi.mock('@/lib/hooks/use-skill-evaluations', () => ({
  useSkillEvaluationSets: (...args: readonly unknown[]) => mockUseSkillEvaluationSets(...args),
  useSkillEvaluationRuns: (...args: readonly unknown[]) => mockUseSkillEvaluationRuns(...args),
  useEstimateSkillEvaluationRun: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useCreateSkillEvaluationRun: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useCancelSkillEvaluationRun: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
}))

function buildSkill(overrides: Partial<Skill> = {}): Skill {
  return {
    id: 'skill-1',
    name: '회의록 정리',
    slug: 'meeting-notes',
    description: '회의록에서 액션 아이템을 정리합니다.',
    kind: 'package',
    version: '0.1.0',
    storage_path: null,
    content_hash: 'hash-current',
    size_bytes: 1000,
    used_by_count: 0,
    package_metadata: null,
    credential_requirements: null,
    execution_profile: null,
    current_revision_id: null,
    latest_evaluation_summary: {
      status: 'completed',
      latest_run_id: 'run-1',
      evaluation_set_id: 'set-1',
      pass_rate: 0.9,
      skill_content_hash: 'hash-current',
      created_at: '2026-06-01T00:00:00Z',
      completed_at: '2026-06-01T00:01:00Z',
    },
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

describe('SkillDetailDialog', () => {
  it('renders one shell body and footer for the evaluation tab', () => {
    mockUseSkill.mockReturnValue({ data: buildSkill() })
    mockUseSkillEvaluationSets.mockReturnValue({
      data: [],
      isLoading: false,
    })
    mockUseSkillEvaluationRuns.mockReturnValue({
      data: [],
      isLoading: false,
    })

    render(
      <SkillDetailDialog skillId="skill-1" open onOpenChange={vi.fn()} initialTab="evaluation" />,
    )

    expect(document.body.querySelectorAll('.moldy-dialog-body')).toHaveLength(1)
    expect(document.body.querySelectorAll('.moldy-dialog-footer')).toHaveLength(1)
    expect(screen.getByText('아직 평가 세트가 없습니다')).toBeInTheDocument()
  })
})
