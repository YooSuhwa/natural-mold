import { beforeEach, describe, expect, it, vi } from 'vitest'

import { render, screen, userEvent } from '../../../../tests/test-utils'
import type { SkillEvaluationRunEstimate, SkillEvaluationSet } from '@/lib/types/skill-evaluation'

import { SkillEvaluationTab } from '../skill-evaluation-tab'

const mockUseSkillEvaluationSets = vi.fn()
const mockUseSkillEvaluationRuns = vi.fn()
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
  useSkillEvaluationRuns: (...args: readonly unknown[]) => mockUseSkillEvaluationRuns(...args),
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
  // Phase 3 hooks — the tab now also renders version stats + case feedback.
  useSkillEvaluationVersionStats: () => ({ data: [], isLoading: false }),
  useSkillEvaluationCaseFeedback: () => ({ data: [], isLoading: false }),
  useUpsertSkillCaseFeedback: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteSkillCaseFeedback: () => ({ mutate: vi.fn(), isPending: false }),
  useInvalidateSkillMetricsOnRunCompletion: () => {},
}))

vi.mock('@/lib/hooks/use-skill-usage', () => ({
  useSkillUsage: () => ({ data: undefined, isLoading: false }),
}))

vi.mock('@/lib/hooks/use-skill-feedback', () => ({
  useSkillFeedback: () => ({ data: undefined, isLoading: false }),
  useUpsertSkillFeedback: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteSkillFeedback: () => ({ mutate: vi.fn(), isPending: false }),
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
    mockUseSkillEvaluationRuns.mockReset()
    mockUseSkillEvaluationRuns.mockReturnValue({ data: [], isLoading: false })
    mockEstimateRun.mockClear()
    mockCreateRun.mockReset()
    mockCancelRun.mockReset()
  })

  it('renders without owning the dialog body or footer', () => {
    mockUseSkillEvaluationSets.mockReturnValue({ data: [], isLoading: false })

    const result = render(<SkillEvaluationTab skillId="skill-1" />)

    expect(result.container.querySelector('.moldy-dialog-body')).not.toBeInTheDocument()
    expect(result.container.querySelector('.moldy-dialog-footer')).not.toBeInTheDocument()
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

    render(<SkillEvaluationTab skillId="skill-1" />)

    await user.click(screen.getByRole('button', { name: '품질 평가 평가 취소' }))

    expect(mockCancelRun).toHaveBeenCalledWith({
      runId: 'run-1',
      data: { reason: 'user_requested' },
    })
    expect(mockCreateRun).not.toHaveBeenCalled()
  })

  it('renders a generated evaluation set before its first run', async () => {
    const user = userEvent.setup()
    mockUseSkillEvaluationSets.mockReturnValue({
      data: [
        buildEvaluationSet({
          id: 'set-generated',
          name: '생성된 품질 평가',
          description: 'Builder가 만든 평가 세트입니다.',
          latest_run: null,
        }),
      ],
      isLoading: false,
    })

    render(<SkillEvaluationTab skillId="skill-1" />)

    expect(screen.getByText('생성된 품질 평가')).toBeInTheDocument()
    expect(screen.getByText('Builder가 만든 평가 세트입니다.')).toBeInTheDocument()
    expect(screen.getByText('평가 없음')).toBeInTheDocument()
    expect(screen.getByText('1개 케이스')).toBeInTheDocument()
    expect(screen.getByText('아직 실행 이력이 없습니다.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '생성된 품질 평가 평가 다시 실행' }))

    expect(mockEstimateRun).toHaveBeenCalledOnce()
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

    render(<SkillEvaluationTab skillId="skill-1" />)

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
        needsCredentialSetup
        onOpenCredentials={openCredentials}
      />,
    )

    await user.click(screen.getByRole('button', { name: '품질 평가 자격증명 연결' }))

    expect(openCredentials).toHaveBeenCalledOnce()
    expect(mockEstimateRun).not.toHaveBeenCalled()
    expect(mockCreateRun).not.toHaveBeenCalled()
  })

  it('shows stale latest summary, run history, and selected run details', async () => {
    const user = userEvent.setup()
    const latestRun = {
      id: 'run-stale',
      skill_id: 'skill-1',
      evaluation_set_id: 'set-1',
      status: 'completed' as const,
      skill_content_hash: 'hash-before-edit',
      summary: {
        pass_rate: 0.74,
        trigger_accuracy: 0.5,
        average_duration_ms: 1250,
        token_delta: -18,
      },
      benchmark: {
        duration_delta_ms: -220,
      },
      case_results: [
        {
          name: '회의록 케이스',
          status: 'passed',
          grader_feedback: '담당자와 마감일을 찾았습니다.',
          evidence: '액션 아이템 표',
        },
      ],
      created_at: '2026-06-01T00:00:00Z',
      updated_at: '2026-06-01T00:01:00Z',
      completed_at: '2026-06-01T00:01:00Z',
    }
    const olderRun = {
      ...latestRun,
      id: 'run-older',
      skill_content_hash: 'hash-current',
      summary: { pass_rate: 0.91 },
      created_at: '2026-05-31T00:00:00Z',
      updated_at: '2026-05-31T00:01:00Z',
      completed_at: '2026-05-31T00:01:00Z',
    }
    mockUseSkillEvaluationSets.mockReturnValue({
      data: [
        buildEvaluationSet({
          latest_run: latestRun,
        }),
      ],
      isLoading: false,
    })
    mockUseSkillEvaluationRuns.mockReturnValue({
      data: [olderRun, latestRun],
      isLoading: false,
    })

    render(<SkillEvaluationTab skillId="skill-1" skillContentHash="hash-current" />)

    expect(screen.getAllByText('재평가 필요')).toHaveLength(2)
    expect(screen.getByText('실행 이력')).toBeInTheDocument()
    expect(screen.getByText('선택한 실행 상세')).toBeInTheDocument()
    expect(screen.getByText('통과율 74%')).toBeInTheDocument()
    expect(screen.getByText('트리거 정확도 50%')).toBeInTheDocument()
    expect(screen.getByText('평균 1.3초')).toBeInTheDocument()
    expect(screen.getByText('토큰 -18')).toBeInTheDocument()
    // Benchmark deltas are now rendered only by SkillBenchmarkPanel (which
    // needs a with_skill_pass_rate) — the legacy duration-delta line was
    // removed to stop the double render (review finding).
    expect(screen.getByText('담당자와 마감일을 찾았습니다.')).toBeInTheDocument()
    expect(screen.getByText('액션 아이템 표')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'run-older 실행 보기' }))

    expect(screen.getByText('통과율 91%')).toBeInTheDocument()
  })
})
