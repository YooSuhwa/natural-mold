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

describe('SkillEvaluationRunDetail', () => {
  it('shows failure details for failed evaluation runs', () => {
    render(<SkillEvaluationRunDetail run={failedRun()} />)

    expect(screen.getByText('실패 원인')).toBeInTheDocument()
    expect(screen.getByText('runner failed before grading')).toBeInTheDocument()
  })
})
