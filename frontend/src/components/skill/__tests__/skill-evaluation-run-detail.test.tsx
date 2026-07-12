import { describe, expect, it } from 'vitest'

import { render, screen } from '../../../../tests/test-utils'
import type { SkillEvaluationRun } from '@/lib/types/skill-evaluation'

import { SkillEvaluationRunDetail } from '../skill-evaluation-run-detail'

function failedRun(): SkillEvaluationRun {
  return {
    id: 'run-failed',
    skill_id: 'skill-1',
    evaluation_set_id: 'set-1',
    status: 'failed',
    summary: null,
    benchmark: null,
    case_results: null,
    error_message: 'runner failed before grading',
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:01:00Z',
    completed_at: '2026-06-01T00:01:00Z',
  }
}

function completedRun(usage: SkillEvaluationRun['usage']): SkillEvaluationRun {
  return {
    id: 'run-1',
    skill_id: 'skill-1',
    evaluation_set_id: 'set-1',
    status: 'completed',
    summary: { pass_rate: 1 },
    benchmark: null,
    usage,
    case_results: [],
    error_message: null,
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-01T00:01:00Z',
    completed_at: '2026-06-01T00:01:00Z',
  }
}

describe('SkillEvaluationRunDetail', () => {
  it('shows failure details for failed evaluation runs', () => {
    render(<SkillEvaluationRunDetail run={failedRun()} />)

    expect(screen.getByText('실패 원인')).toBeInTheDocument()
    expect(screen.getByText('runner failed before grading')).toBeInTheDocument()
  })

  it('renders measured token totals when tokens are measured', () => {
    render(
      <SkillEvaluationRunDetail
        run={completedRun({
          measured: true,
          tokens_measured: true,
          model_calls: 6,
          tokens_in: 3000,
          tokens_out: 500,
          cost_usd: 0.02,
        })}
      />,
    )
    const line = screen.getByTestId('run-usage-line')
    expect(line).toHaveTextContent('3,500')
    expect(line).not.toHaveTextContent('미측정')
  })

  it('shows "토큰 미측정" instead of 0 tokens when usage_metadata was absent', () => {
    // review R5 — a priced/real run with no usage_metadata must not present 0
    // tokens as a measured quantity (unknown ≠ zero).
    render(
      <SkillEvaluationRunDetail
        run={completedRun({
          measured: true,
          tokens_measured: false,
          model_calls: 6,
          tokens_in: 0,
          tokens_out: 0,
          cost_usd: null,
        })}
      />,
    )
    const line = screen.getByTestId('run-usage-line')
    expect(line).toHaveTextContent('토큰 미측정')
    expect(line).not.toHaveTextContent('0 토큰')
  })
})
