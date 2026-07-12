import { describe, expect, it, vi } from 'vitest'

import { render, screen, userEvent } from '../../../../tests/test-utils'
import type { SkillEvaluationRun, SkillEvaluationVersionStats } from '@/lib/types/skill-evaluation'
import type { SkillFeedbackSummary } from '@/lib/types/skill-feedback'
import type { SkillUsageSummary } from '@/lib/types/skill-usage'

import { SkillBenchmarkPanel } from '../skill-benchmark-panel'
import { SkillFeedbackCard } from '../skill-feedback-card'
import { SkillUsageSummaryCard } from '../skill-usage-summary-card'
import { SkillVersionPassRatePanel } from '../skill-version-pass-rate-panel'

const mockVersionStats = vi.fn<() => { data?: SkillEvaluationVersionStats[]; isLoading: boolean }>(
  () => ({ data: [], isLoading: false }),
)
vi.mock('@/lib/hooks/use-skill-evaluations', () => ({
  useSkillEvaluationVersionStats: () => mockVersionStats(),
}))

const mockUsage = vi.fn<() => { data?: SkillUsageSummary; isLoading: boolean }>(() => ({
  data: undefined,
  isLoading: false,
}))
vi.mock('@/lib/hooks/use-skill-usage', () => ({
  useSkillUsage: () => mockUsage(),
}))

const mockFeedbackSummary = vi.fn<() => { data?: SkillFeedbackSummary; isLoading: boolean }>(
  () => ({ data: undefined, isLoading: false }),
)
const mockUpsertFeedback = vi.fn()
const mockDeleteFeedback = vi.fn()
vi.mock('@/lib/hooks/use-skill-feedback', () => ({
  useSkillFeedback: () => mockFeedbackSummary(),
  useUpsertSkillFeedback: () => ({ mutate: mockUpsertFeedback, isPending: false }),
  useDeleteSkillFeedback: () => ({ mutate: mockDeleteFeedback, isPending: false }),
}))

function buildRun(benchmark: SkillEvaluationRun['benchmark']): SkillEvaluationRun {
  return {
    id: 'run-1',
    skill_id: 'skill-1',
    evaluation_set_id: 'set-1',
    status: 'completed',
    benchmark,
    created_at: '2026-07-12T00:00:00Z',
    updated_at: '2026-07-12T00:00:00Z',
  }
}

describe('SkillBenchmarkPanel', () => {
  it('renders measured A/B bars with deltas', () => {
    render(
      <SkillBenchmarkPanel
        run={buildRun({
          measured: true,
          baseline_skipped: false,
          with_skill_pass_rate: 0.95,
          without_skill_pass_rate: 0.3,
          pass_rate_delta: 0.65,
          token_delta: -120,
          duration_delta_ms: 850,
        })}
      />,
    )

    expect(screen.getByTestId('benchmark-measured')).toHaveTextContent('실측')
    const bars = screen.getAllByTestId('skill-metric-bar')
    expect(bars).toHaveLength(2)
    expect(bars[0]).toHaveTextContent('스킬 사용')
    expect(bars[0]).toHaveTextContent('95%')
    expect(bars[1]).toHaveTextContent('스킬 없이')
    expect(bars[1]).toHaveTextContent('30%')
    expect(screen.getByText('통과율 차이 +65%')).toBeInTheDocument()
  })

  it('renders a negative pass-rate delta as a regression, not clamped to 0%', () => {
    render(
      <SkillBenchmarkPanel
        run={buildRun({
          measured: true,
          baseline_skipped: false,
          with_skill_pass_rate: 0.4,
          without_skill_pass_rate: 0.7,
          pass_rate_delta: -0.3,
        })}
      />,
    )

    // A skill that performs WORSE than baseline must show the negative delta,
    // never a clamped "0%" that hides the regression (review finding).
    expect(screen.getByText('통과율 차이 -30%')).toBeInTheDocument()
  })

  it('labels legacy runs as estimated and hides missing baseline', () => {
    render(
      <SkillBenchmarkPanel
        run={buildRun({
          with_skill_pass_rate: 0.8,
          without_skill_pass_rate: null,
          pass_rate_delta: null,
        })}
      />,
    )

    expect(screen.getByTestId('benchmark-estimated')).toHaveTextContent('추정')
    expect(screen.getAllByTestId('skill-metric-bar')).toHaveLength(1)
  })

  it('renders nothing without a benchmark', () => {
    const { container } = render(<SkillBenchmarkPanel run={buildRun(null)} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('SkillVersionPassRatePanel', () => {
  it('renders one bar per version with run counts', () => {
    mockVersionStats.mockReturnValue({
      data: [
        {
          skill_version: '1.0.0',
          content_hash: 'hash-1',
          run_count: 2,
          latest_pass_rate: 0.5,
          avg_pass_rate: 0.45,
          latest_pass_rate_delta: 0.2,
          latest_measured: true,
          first_run_at: '2026-07-10T00:00:00Z',
          last_run_at: '2026-07-10T01:00:00Z',
        },
        {
          skill_version: '1.1.0',
          content_hash: 'hash-2',
          run_count: 1,
          latest_pass_rate: 1,
          avg_pass_rate: 1,
          latest_pass_rate_delta: 0.6,
          latest_measured: true,
          first_run_at: '2026-07-11T00:00:00Z',
          last_run_at: '2026-07-11T00:00:00Z',
        },
      ],
      isLoading: false,
    })

    render(<SkillVersionPassRatePanel skillId="skill-1" />)

    const bars = screen.getAllByTestId('skill-metric-bar')
    expect(bars).toHaveLength(2)
    expect(bars[0]).toHaveTextContent('1.0.0')
    expect(bars[0]).toHaveTextContent('50%')
    expect(bars[0]).toHaveTextContent('런 2회')
    expect(bars[1]).toHaveTextContent('1.1.0')
    expect(bars[1]).toHaveTextContent('100%')
  })

  it('renders nothing when no stats exist', () => {
    mockVersionStats.mockReturnValue({ data: [], isLoading: false })
    const { container } = render(<SkillVersionPassRatePanel skillId="skill-1" />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('SkillUsageSummaryCard', () => {
  it('shows totals and marks unknown cost honestly', () => {
    mockUsage.mockReturnValue({
      data: {
        skill_id: 'skill-1',
        days: 30,
        tokens_in: 1200,
        tokens_out: 300,
        cost_usd: 0,
        priced_event_count: 0,
        unpriced_token_event_count: 2,
        evaluation_run_count: 3,
        chat_execution_count: 5,
        daily: [],
      },
      isLoading: false,
    })

    render(<SkillUsageSummaryCard skillId="skill-1" />)

    const card = screen.getByTestId('skill-usage-summary-card')
    expect(card).toHaveTextContent('1,500')
    // priced_event_count 0 → cost must read "unknown", never $0 (모름 ≠ 무료).
    expect(card).toHaveTextContent('단가 미설정')
    expect(card).toHaveTextContent('단가가 없는 이벤트 2건은 비용에 포함되지 않았습니다.')
  })
})

describe('SkillFeedbackCard', () => {
  it('submits an up rating and toggles delete on repeat', async () => {
    mockFeedbackSummary.mockReturnValue({
      data: { skill_id: 'skill-1', up_count: 1, down_count: 0, mine: null },
      isLoading: false,
    })
    const user = userEvent.setup()
    render(<SkillFeedbackCard skillId="skill-1" />)

    await user.click(screen.getByTestId('skill-feedback-up'))
    expect(mockUpsertFeedback).toHaveBeenCalledWith(
      { rating: 'up', comment: null },
      expect.anything(),
    )

    mockFeedbackSummary.mockReturnValue({
      data: {
        skill_id: 'skill-1',
        up_count: 2,
        down_count: 0,
        mine: { rating: 'up', comment: null, updated_at: '2026-07-12T00:00:00Z' },
      },
      isLoading: false,
    })
    render(<SkillFeedbackCard skillId="skill-1" />)
    await user.click(screen.getAllByTestId('skill-feedback-up')[1])
    expect(mockDeleteFeedback).toHaveBeenCalled()
  })
})
