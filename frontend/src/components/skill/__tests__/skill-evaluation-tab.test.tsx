import { beforeEach, describe, expect, it, vi } from 'vitest'

import { render, screen, userEvent } from '../../../../tests/test-utils'
import type { SkillEvaluationRunEstimate, SkillEvaluationSet } from '@/lib/types/skill-evaluation'

import { SkillEvaluationTab } from '../skill-evaluation-tab'

const mockUseSkillEvaluationSets = vi.fn()
const mockEstimateRun = vi.fn(
  (
    _variables: undefined,
    options?: { readonly onSuccess?: (data: SkillEvaluationRunEstimate) => void },
  ) => {
    options?.onSuccess?.(evaluationEstimate)
  },
)
const mockCreateRun = vi.fn()
const mockCancelRun = vi.fn()

const evaluationEstimate: SkillEvaluationRunEstimate = {
  case_count: 2,
  model_call_count: 4,
  estimated_seconds: 12,
  timeout_seconds: 60,
  estimated_cost_usd: 0.0123,
  uses_baseline_comparison: true,
}

vi.mock('@/lib/hooks/use-skill-evaluations', () => ({
  useSkillEvaluationSets: (...args: readonly unknown[]) => mockUseSkillEvaluationSets(...args),
  useEstimateSkillEvaluationRun: () => ({
    mutate: mockEstimateRun,
    isPending: false,
  }),
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
    mockEstimateRun.mockClear()
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

    expect(mockEstimateRun).toHaveBeenCalledOnce()
    expect(mockCreateRun).not.toHaveBeenCalled()
    expect(screen.getByRole('alertdialog', { name: '평가 실행 확인' })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '평가 실행' }))

    expect(mockCreateRun).toHaveBeenCalledOnce()
    expect(mockCancelRun).not.toHaveBeenCalled()
  })

  it('opens credentials instead of estimating a run when required credentials are missing', async () => {
    const user = userEvent.setup()
    const openCredentials = vi.fn()
    mockUseSkillEvaluationSets.mockReturnValue({
      data: [
        buildEvaluationSet({
          latest_run: {
            id: 'run-3',
            skill_id: 'skill-1',
            evaluation_set_id: 'set-1',
            status: 'failed',
            summary: { pass_rate: 0.2 },
            created_at: '2026-06-01T00:00:00Z',
            updated_at: '2026-06-01T00:01:00Z',
            completed_at: '2026-06-01T00:01:00Z',
          },
        }),
      ],
      isLoading: false,
    })

    render(
      <SkillEvaluationTab
        skillId="skill-1"
        onClose={vi.fn()}
        needsCredentialSetup
        onOpenCredentials={openCredentials}
      />,
    )

    await user.click(screen.getByRole('button', { name: '품질 평가 자격증명 연결' }))

    expect(openCredentials).toHaveBeenCalledOnce()
    expect(mockEstimateRun).not.toHaveBeenCalled()
    expect(mockCreateRun).not.toHaveBeenCalled()
  })
})
