import { describe, it, expect } from 'vitest'
import { builderApi } from '@/lib/api/builder'
import { mockBuilderSession, mockAgent } from '../../mocks/fixtures'

describe('builderApi', () => {
  it('start()는 POST /api/builder를 호출하고 세션을 반환한다', async () => {
    const session = await builderApi.start('뉴스 요약 에이전트')

    expect(session.id).toBe(mockBuilderSession.id)
    expect(session.user_request).toBe('뉴스 요약 에이전트')
    expect(session.status).toBe('building')
  })

  it('getSession()은 GET /api/builder/{id}로 세션을 조회한다', async () => {
    const session = await builderApi.getSession('builder-session-1')

    expect(session.id).toBe('builder-session-1')
    expect(session.status).toBe(mockBuilderSession.status)
    expect(session.intent).not.toBeNull()
    expect(session.draft_config).not.toBeNull()
  })

  it('confirm()은 POST /api/builder/{id}/confirm으로 에이전트를 생성한다', async () => {
    const agent = await builderApi.confirm('builder-session-1')

    expect(agent.id).toBe('agent-from-builder')
    expect(agent.name).toBe(mockAgent.name)
  })
})
