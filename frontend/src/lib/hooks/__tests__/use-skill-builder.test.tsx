import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { skillBuilderApi } from '@/lib/api/skill-builder'
import { skillEvaluationsApi } from '@/lib/api/skill-evaluations'
import { skillRevisionsApi } from '@/lib/api/skill-revisions'
import { useConfirmSkillBuilderSession, useRunSkillBuilderEvaluation } from '../use-skill-builder'
import { useCreateSkillEvaluationRun } from '../use-skill-evaluations'
import { useRollbackSkillRevision } from '../use-skill-revisions'

vi.mock('@/lib/api/skill-builder', () => ({
  skillBuilderApi: { confirm: vi.fn(), runEvaluation: vi.fn() },
}))

vi.mock('@/lib/api/skill-evaluations', () => ({
  skillEvaluationsApi: { createRun: vi.fn() },
}))

vi.mock('@/lib/api/skill-revisions', () => ({
  skillRevisionsApi: { rollback: vi.fn() },
}))

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
}

const skillResponse = {
  id: 'skill-1',
  name: '회의록 액션 아이템',
  slug: 'meeting-actions',
  description: null,
  kind: 'package',
  version: null,
  storage_path: null,
  content_hash: 'hash-1',
  size_bytes: 0,
  used_by_count: 0,
  package_metadata: null,
  execution_profile: null,
  last_modified_at: '2026-06-15T00:00:00.000Z',
  created_at: '2026-06-15T00:00:00.000Z',
  updated_at: '2026-06-15T00:00:00.000Z',
} as const

describe('skill builder hooks', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('confirm invalidates skill list, detail, files, and content caches', async () => {
    const queryClient = createTestQueryClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    vi.mocked(skillBuilderApi.confirm).mockResolvedValue(skillResponse)

    const { result } = renderHook(() => useConfirmSkillBuilderSession(), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync('session-1')
    })

    expect(skillBuilderApi.confirm).toHaveBeenCalledWith('session-1')
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['skills'] })
    })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['skills', 'skill-1'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['skills', 'skill-1', 'files'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['skills', 'skill-1', 'content'] })
  })

  it('evaluation rerun invalidates installed-skill evaluation and detail caches', async () => {
    const queryClient = createTestQueryClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    vi.mocked(skillEvaluationsApi.createRun).mockResolvedValue({
      id: 'run-1',
      skill_id: 'skill-1',
      evaluation_set_id: 'set-1',
      status: 'queued',
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
    })

    const { result } = renderHook(() => useCreateSkillEvaluationRun('skill-1', 'set-1'), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync()
    })

    expect(skillEvaluationsApi.createRun).toHaveBeenCalledWith('skill-1', 'set-1')
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['skills', 'skill-1', 'evaluations'] })
    })
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: ['skills', 'skill-1', 'evaluations', 'set-1', 'runs'],
    })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['skills', 'skill-1'] })
  })

  it('rollback invalidates skill content, evaluation, and revision caches', async () => {
    const queryClient = createTestQueryClient()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    vi.mocked(skillRevisionsApi.rollback).mockResolvedValue({
      skill: skillResponse,
      revision: {
        id: 'revision-2',
        skill_id: 'skill-1',
        revision_number: 2,
        operation: 'rollback',
        skill_version: null,
        content_hash: 'hash-1',
        size_bytes: 0,
        file_count: 1,
        changelog_summary: '되돌림',
        created_at: '2026-06-15T00:00:00.000Z',
      },
    })

    const { result } = renderHook(() => useRollbackSkillRevision('skill-1'), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync('revision-1')
    })

    expect(skillRevisionsApi.rollback).toHaveBeenCalledWith('skill-1', 'revision-1')
    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['skills', 'skill-1', 'revisions'] })
    })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['skills'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['skills', 'skill-1'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['skills', 'skill-1', 'files'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['skills', 'skill-1', 'content'] })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['skills', 'skill-1', 'evaluations'] })
  })

  it('builder evaluation run stores the refreshed session in cache', async () => {
    const queryClient = createTestQueryClient()
    vi.mocked(skillBuilderApi.runEvaluation).mockResolvedValue({
      id: 'session-1',
      user_id: 'user-1',
      user_request: '회의록 액션 아이템 스킬',
      mode: 'create',
      status: 'review',
      current_phase: 3,
      draft_package: null,
      validation_result: null,
      compatibility_result: null,
      changelog_draft: null,
      eval_result: { summary: { case_count: 3 } },
      trigger_eval_result: null,
      finalized_skill_id: null,
      error_message: null,
      created_at: '2026-06-15T00:00:00.000Z',
      updated_at: '2026-06-15T00:00:00.000Z',
    })

    const { result } = renderHook(() => useRunSkillBuilderEvaluation('session-1'), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync()
    })

    expect(skillBuilderApi.runEvaluation).toHaveBeenCalledWith('session-1')
    expect(queryClient.getQueryData(['skill-builder', 'session-1'])).toMatchObject({
      eval_result: { summary: { case_count: 3 } },
    })
  })
})
