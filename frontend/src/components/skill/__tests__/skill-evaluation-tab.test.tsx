import { beforeEach, describe, expect, it, vi } from 'vitest'

import { render, screen, userEvent } from '../../../../tests/test-utils'
import type { SkillEvaluationSet } from '@/lib/types/skill-evaluation'

import { SkillEvaluationTab } from '../skill-evaluation-tab'

const mockUseSkillEvaluationSets = vi.fn()
const mockCreateRun = vi.fn()
const mockCancelRun = vi.fn()

vi.mock('@/lib/hooks/use-skill-evaluations', () => ({
  useSkillEvaluationSets: (...args: readonly unknown[]) => mockUseSkillEvaluationSets(...args),
  useCreateSkillEvaluationRun: () => ({
    mutate: mockCreateRun,
    isPending: false,
  }),
  useCancelSkillEvaluationRun: () => ({
    mutate: mockCancelRun,
    isPending: false,
  }),
}))

function buildEvaluationSet(overrides: Partial<SkillEvaluationSet>): SkillEvaluationSet {
  return {
    id: 'set-1',
    skill_id: 'skill-1',
    name: '품질 평가',
    description: '핵심 응답 품질을 확인합니다.',
    source_kind: 'generated',
    evals: [{ input: '질문', expected: '답변' }],
    expectations_schema_version: 1,
    latest_run: null,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:00:00Z',
    ...overrides,
  }
}

describe('SkillEvaluationTab', () => {
  beforeEach(() => {
    mockUseSkillEvaluationSets.mockReset()
    mockCreateRun.mockReset()
    mockCancelRun.mockReset()
  })

  it('cancels the latest active evaluation run', async () => {
    const user = userEvent.setup()
    mockUseSkillEvaluationSets.mockReturnValue({
      data: [
        buildEvaluationSet({
          latest_run: {
            id: 'run-1',
            skill_id: 'skill-1',
            evaluation_set_id: 'set-1',
            status: 'running',
            summary: { pass_rate: 0.5 },
            created_at: '2026-06-01T00:00:00Z',
            updated_at: '2026-06-01T00:00:10Z',
          },
        }),
      ],
      isLoading: false,
    })

    render(<SkillEvaluationTab skillId="skill-1" onClose={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: '품질 평가 평가 취소' }))

    expect(mockCancelRun).toHaveBeenCalledWith({
      runId: 'run-1',
      data: { reason: 'user_requested' },
    })
    expect(mockCreateRun).not.toHaveBeenCalled()
  })

  it('reruns completed evaluation sets', async () => {
    const user = userEvent.setup()
    mockUseSkillEvaluationSets.mockReturnValue({
      data: [
        buildEvaluationSet({
          latest_run: {
            id: 'run-2',
            skill_id: 'skill-1',
            evaluation_set_id: 'set-1',
            status: 'completed',
            summary: { pass_rate: 0.92 },
            created_at: '2026-06-01T00:00:00Z',
            updated_at: '2026-06-01T00:01:00Z',
            completed_at: '2026-06-01T00:01:00Z',
          },
        }),
      ],
      isLoading: false,
    })

    render(<SkillEvaluationTab skillId="skill-1" onClose={vi.fn()} />)

    await user.click(screen.getByRole('button', { name: '품질 평가 평가 다시 실행' }))

    expect(mockCreateRun).toHaveBeenCalledOnce()
    expect(mockCancelRun).not.toHaveBeenCalled()
  })
})
