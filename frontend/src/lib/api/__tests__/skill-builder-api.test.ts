import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiFetch } from '@/lib/api/client'
import { skillBuilderApi } from '../skill-builder'
import { skillEvaluationsApi } from '../skill-evaluations'
import { skillRevisionsApi } from '../skill-revisions'

vi.mock('@/lib/api/client', () => ({
  apiFetch: vi.fn(),
}))

describe('skill builder APIs', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiFetch).mockResolvedValue({})
  })

  it('starts a skill builder session through the dedicated builder endpoint', async () => {
    const request = {
      mode: 'create',
      user_request: '회의록 액션 아이템 스킬을 만들어줘',
    } as const

    await skillBuilderApi.start(request)

    expect(apiFetch).toHaveBeenCalledWith('/api/skill-builder', {
      method: 'POST',
      body: JSON.stringify(request),
    })
  })

  it('confirms a builder session without routing through normal chat APIs', async () => {
    await skillBuilderApi.confirm('session-1')

    expect(apiFetch).toHaveBeenCalledWith('/api/skill-builder/session-1/confirm', {
      method: 'POST',
    })
  })

  it('creates and reruns installed-skill evaluations under the skill resource', async () => {
    const request = {
      name: '기본 평가',
      description: null,
      evals: [{ prompt: '회의록에서 할 일을 찾아줘' }],
    }

    await skillEvaluationsApi.createSet('skill-1', request)
    await skillEvaluationsApi.createRun('skill-1', 'set-1', true)

    expect(apiFetch).toHaveBeenCalledWith('/api/skills/skill-1/evaluations', {
      method: 'POST',
      body: JSON.stringify(request),
    })
    expect(apiFetch).toHaveBeenCalledWith('/api/skills/skill-1/evaluations/set-1/runs', {
      method: 'POST',
      body: JSON.stringify({ baseline_comparison: true }),
    })
  })

  it('rolls back a skill revision through the revision endpoint', async () => {
    await skillRevisionsApi.rollback('skill-1', 'revision-1')

    expect(apiFetch).toHaveBeenCalledWith('/api/skills/skill-1/revisions/revision-1/rollback', {
      method: 'POST',
    })
  })
})
