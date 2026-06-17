import { render, screen } from '../../../../tests/test-utils'
import { describe, expect, it } from 'vitest'

import { SkillEvaluationSummaryBadge } from '../skill-evaluation-summary-badge'
import { SkillHealthBadge } from '../skill-health-badge'

describe('skill quality badges', () => {
  it('renders health labels from the backend summary', () => {
    render(
      <SkillHealthBadge
        health={{
          state: 'needs_credentials',
          label: '자격증명 필요',
          reason: 'Missing required credential bindings.',
          severity: 'warning',
        }}
      />,
    )

    expect(screen.getByText('자격증명 필요')).toBeInTheDocument()
  })

  it('renders pass rates as compact evaluation summaries', () => {
    render(
      <SkillEvaluationSummaryBadge
        summary={{
          status: 'completed',
          latest_run_id: 'run-1',
          evaluation_set_id: 'set-1',
          pass_rate: 0.92,
          skill_content_hash: 'hash-1',
          created_at: '2026-06-01T00:00:00Z',
          completed_at: '2026-06-01T00:01:00Z',
        }}
      />,
    )

    expect(screen.getByText('평가 92%')).toBeInTheDocument()
  })

  it('renders missing evaluation states without a pass rate', () => {
    render(
      <SkillEvaluationSummaryBadge
        summary={{
          status: 'missing',
          latest_run_id: null,
          evaluation_set_id: null,
          pass_rate: null,
          skill_content_hash: null,
          created_at: null,
          completed_at: null,
        }}
      />,
    )

    expect(screen.getByText('평가 없음')).toBeInTheDocument()
  })

  it('prioritizes cancelled state over stale pass-rate data', () => {
    render(
      <SkillEvaluationSummaryBadge
        summary={{
          status: 'cancelled',
          latest_run_id: 'run-1',
          evaluation_set_id: 'set-1',
          pass_rate: 1,
          skill_content_hash: 'hash-1',
          created_at: '2026-06-01T00:00:00Z',
          completed_at: '2026-06-01T00:01:00Z',
        }}
      />,
    )

    expect(screen.getByText('평가 취소')).toBeInTheDocument()
    expect(screen.queryByText('평가 100%')).not.toBeInTheDocument()
  })
})
