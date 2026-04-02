import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../setup'
import { usageApi } from '@/lib/api/usage'
import { mockUsageSummary } from '../../mocks/fixtures'

const API_BASE = 'http://localhost:8001'

describe('usageApi', () => {
  it('summary() returns usage summary', async () => {
    const summary = await usageApi.summary()
    expect(summary).toEqual(mockUsageSummary)
    expect(summary.total_tokens).toBe(150000)
    expect(summary.by_agent).toHaveLength(2)
  })

  it('summary() with period sends query parameter', async () => {
    let capturedUrl = ''
    server.use(
      http.get(`${API_BASE}/api/usage/summary`, ({ request }) => {
        capturedUrl = request.url
        return HttpResponse.json(mockUsageSummary)
      }),
    )

    await usageApi.summary('30d')
    expect(capturedUrl).toContain('period=30d')
  })

  it('agentUsage() returns usage for a specific agent', async () => {
    const usage = await usageApi.agentUsage('agent-1')
    expect(usage).toEqual({ total_tokens: 100000, estimated_cost: 0.85 })
  })
})
