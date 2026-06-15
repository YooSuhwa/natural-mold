import { describe, expect, it } from 'vitest'

import { skillEvaluationRunsRefetchInterval } from '../use-skill-evaluations'
import type { SkillEvaluationRun } from '@/lib/types/skill-evaluation'

function run(status: SkillEvaluationRun['status']): SkillEvaluationRun {
  return {
    id: `run-${status}`,
    skill_id: 'skill-1',
    evaluation_set_id: 'set-1',
    status,
    skill_version: null,
    skill_content_hash: 'hash-1',
    runner_model: null,
    summary: null,
    benchmark: null,
    case_results: null,
    error_message: null,
    cancellation_requested_at: null,
    cancellation_reason: null,
    started_at: null,
    completed_at: null,
    created_at: '2026-06-15T00:00:00.000Z',
    updated_at: '2026-06-15T00:00:00.000Z',
  }
}

describe('skill evaluation hooks', () => {
  it('polls run history while any evaluation run is active', () => {
    expect(skillEvaluationRunsRefetchInterval([run('queued')])).toBe(1000)
    expect(skillEvaluationRunsRefetchInterval([run('running')])).toBe(1000)
    expect(skillEvaluationRunsRefetchInterval([run('grading')])).toBe(1000)
  })

  it('stops polling when all evaluation runs are terminal', () => {
    expect(skillEvaluationRunsRefetchInterval([run('completed'), run('failed')])).toBe(false)
    expect(skillEvaluationRunsRefetchInterval([])).toBe(false)
    expect(skillEvaluationRunsRefetchInterval(undefined)).toBe(false)
  })
})
