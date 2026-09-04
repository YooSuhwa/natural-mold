import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createElement, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

import {
  skillEvaluationKeys,
  skillEvaluationRunsRefetchInterval,
  useInvalidateSkillMetricsOnRunCompletion,
} from '../use-skill-evaluations'
import { skillQueryKeys } from '@/lib/query-keys/skills'
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

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children)
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

  it('invalidates skill usage, version stats, and feedback when an active run completes', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const { rerender } = renderHook(
      ({ runs }) => useInvalidateSkillMetricsOnRunCompletion('skill-1', runs),
      {
        initialProps: { runs: [run('running')] },
        wrapper: createWrapper(queryClient),
      },
    )

    invalidateSpy.mockClear()
    rerender({ runs: [run('completed')] })

    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: skillEvaluationKeys.versionStats('skill-1'),
    })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: skillQueryKeys.feedback('skill-1') })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: skillQueryKeys.usageRoot('skill-1') })
  })
})
