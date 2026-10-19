import type { Request } from '@playwright/test'

import { API_BASE, apiDeleteOk, apiGetJson, apiPostJson, expect, loginApi, test } from './fixtures'
import { readinessServer } from './helpers/readiness-server'
import { commandMethod, sendMessage } from './langgraph-v3-helpers'

// Plan 16.5 E2E #4 (G-04): "per_user MCP가 붙은 에이전트: 점검 패널에서 연결하기 → 시작".
// The agent and the chat run are real (scripted model); the readiness, credential and
// binding answers are played by `readinessServer` until the 16.5 enforce-mode role
// fixtures exist. Opt in with E2E_ORG_AUTHZ=1; NOT run in the default lanes yet.

interface IdRow {
  id: string
  provider?: string
}

async function scriptedAgent(request: Parameters<typeof loginApi>[0]) {
  const csrf = await loginApi(request)
  const models = (await apiGetJson(request, `${API_BASE}/api/models`)) as IdRow[]
  const model = models.find((candidate) => candidate.provider === 'e2e_scripted')
  if (!model) throw new Error('Scripted model is not seeded')
  const agent = (await apiPostJson(request, `${API_BASE}/api/agents`, csrf, {
    name: `E2E 인사 도우미 ${Date.now()}`,
    model_id: model.id,
    system_prompt: 'Answer briefly.',
  })) as IdRow
  return { csrf, agentId: agent.id }
}

test.describe('Readiness panel → connect → start (E2E #4)', () => {
  test.skip(process.env.E2E_ORG_AUTHZ !== '1', 'Org-authz role fixtures (plan 16.5) pending')

  test('connects my account from the check panel and then starts the chat', async ({
    page,
    request,
  }) => {
    const { csrf, agentId } = await scriptedAgent(request)
    const server = readinessServer()
    try {
      await server.install(page, agentId)
      await page.goto(`/agents/${agentId}/conversations/new`)

      // 1. The chat screen folds the not-ready agent into a card (S21 "상단 접힘 카드").
      await expect(page.getByText('필수 항목 1개 연결이 필요해요')).toBeVisible({ timeout: 20_000 })
      await page.getByRole('button', { name: '점검 보기' }).click()
      const panel = page.getByRole('dialog')
      await expect(panel.getByText('Slack')).toBeVisible()
      await expect(panel.getByRole('button', { name: '확인', exact: true })).toBeDisabled()

      // 2. 연결하기 → S23: pick my credential of the accepted type.
      await panel.getByRole('button', { name: '연결하기' }).click()
      await page.getByRole('button', { name: '내 Slack 연결' }).click()
      await expect(page.getByText('연결했어요')).toBeVisible()
      expect(server.state.bindCalls).toBe(1)

      // 3. The re-check is ready, so "확인" closes the panel and the chat starts.
      await expect(panel.getByText('모든 항목을 바로 쓸 수 있어요')).toBeVisible()
      await panel.getByRole('button', { name: '확인', exact: true }).click()
      await expect(page.getByRole('dialog')).toHaveCount(0)
      await expect(page.getByText('필수 항목 1개 연결이 필요해요')).toHaveCount(0)
      await sendMessage(page, '휴가 규정 알려줘')
      await expect(
        page.locator('[data-moldy-message-id]').filter({ hasText: '휴가 규정 알려줘' }),
      ).toHaveCount(1, { timeout: 20_000 })
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, csrf)
    }
  })

  test('a 409 agent_not_ready on send opens the check panel by itself', async ({
    page,
    request,
  }) => {
    const { csrf, agentId } = await scriptedAgent(request)
    const server = readinessServer()
    const refused: Request[] = []
    try {
      await server.install(page, agentId)
      await page.route('**/langgraph/threads/*/commands', async (route) => {
        if (commandMethod(route.request()) !== 'run.start') return route.fallback()
        refused.push(route.request())
        await route.fulfill({
          status: 409,
          json: {
            error: {
              code: 'agent_not_ready',
              message: 'agent is not ready',
              details: { ...server.readiness(), overall: 'blocked' },
            },
          },
        })
      })
      await page.goto(`/agents/${agentId}/conversations/new`)
      await expect(page.getByText('필수 항목 1개 연결이 필요해요')).toBeVisible({ timeout: 20_000 })
      await sendMessage(page, '연차 조회해줘')
      await expect(page.getByRole('dialog').getByText('시작 전 점검')).toBeVisible({
        timeout: 15_000,
      })
      expect(refused.length).toBeGreaterThan(0)
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${agentId}`, csrf)
    }
  })
})
