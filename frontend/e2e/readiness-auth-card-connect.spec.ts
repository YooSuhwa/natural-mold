import type { Request } from '@playwright/test'

import { API_BASE, apiDeleteOk, apiGetJson, apiPostJson, expect, loginApi, test } from './fixtures'
import { readinessServer } from './helpers/readiness-server'
import { commandMethod } from './langgraph-v3-helpers'

// Plan 16.5 E2E #5 (G-04 card side): "대화 중 토큰 만료 → 인증 카드 → 연결 → 이어서 실행".
// The conversation is real; its thread state is answered with a pending `auth_required`
// interrupt (G-03 state API shape: tasks[].interrupts[]), as the ConnectionGuard leaves it
// after a mocked MCP 401. The card's 연결하기 opens the S21 check, S23 connects my
// account and the card resumes with `respond: retry`. The run itself (scripted model +
// mock MCP 401) needs the 16.5 enforce fixtures. Opt in with E2E_ORG_AUTHZ=1.

interface IdRow {
  id: string
  provider?: string
}

const INTERRUPT = {
  id: 'intr-auth-1',
  value: {
    type: 'auth_required',
    reason: 'expired',
    attempt: 1,
    allowed_decisions: ['respond'],
    items: [
      {
        resource_type: 'mcp_server',
        resource_name: 'Slack',
        tool_names: ['post_message'],
        credential_id: null,
        fixable_by: 'me',
        connect: { kind: 'binding' },
      },
    ],
  },
}

test.describe('Auth card → connect → continue (E2E #5)', () => {
  test.skip(process.env.E2E_ORG_AUTHZ !== '1', 'Org-authz role fixtures (plan 16.5) pending')

  test('connects from the auth card and resumes the paused run with retry', async ({
    page,
    request,
  }) => {
    const csrf = await loginApi(request)
    const models = (await apiGetJson(request, `${API_BASE}/api/models`)) as IdRow[]
    const model = models.find((candidate) => candidate.provider === 'e2e_scripted')
    if (!model) throw new Error('Scripted model is not seeded')
    const agent = (await apiPostJson(request, `${API_BASE}/api/agents`, csrf, {
      name: `E2E 인증 카드 ${Date.now()}`,
      model_id: model.id,
      system_prompt: 'Post to Slack.',
    })) as IdRow
    const conversation = (await apiPostJson(
      request,
      `${API_BASE}/api/agents/${agent.id}/conversations`,
      csrf,
      { title: 'auth card' },
    )) as IdRow
    const server = readinessServer()
    const answers: Request[] = []
    try {
      await server.install(page, agent.id)
      await page.route(`**/langgraph/threads/${conversation.id}/state**`, async (route) => {
        const real = await route.fetch()
        const body = (await real.json()) as Record<string, unknown>
        const tasks = [{ id: 'run-auth', name: 'interrupted', interrupts: [INTERRUPT] }]
        await route.fulfill({ response: real, json: { ...body, tasks, interrupts: [INTERRUPT] } })
      })
      await page.route('**/langgraph/threads/*/commands', async (route) => {
        if (commandMethod(route.request()) === 'input.respond') answers.push(route.request())
        await route.fallback()
      })

      await page.goto(`/agents/${agent.id}/conversations/${conversation.id}`)
      // 1. The pending interrupt is restored as the S22 card on load.
      await expect(page.getByText('연결이 필요합니다')).toBeVisible({ timeout: 20_000 })
      await expect(page.getByText('Slack', { exact: true }).first()).toBeVisible()

      // 2. 연결하기 → S21 check → S23 connect my account.
      await page.getByRole('button', { name: '연결하기', exact: true }).first().click()
      const panel = page.getByRole('dialog')
      await panel.getByRole('button', { name: '연결하기' }).click()
      await page.getByRole('button', { name: '내 Slack 연결' }).click()
      await expect(page.getByText('연결했어요')).toBeVisible()

      // 3. The card answers `retry` for the same interrupt (G-03/G-06 resume decision).
      await expect.poll(() => answers.length, { timeout: 15_000 }).toBeGreaterThan(0)
      const command = answers[0]?.postDataJSON() as Record<string, unknown>
      expect(JSON.stringify(command)).toContain('"retry"')
      expect(server.state.bindCalls).toBe(1)
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${agent.id}`, csrf)
    }
  })
})
