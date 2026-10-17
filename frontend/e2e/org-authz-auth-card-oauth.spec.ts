import type { Request } from '@playwright/test'

import { API_BASE, apiDeleteOk, apiGetJson, apiPostJson, expect, loginApi, test } from './fixtures'
import { commandMethod } from './langgraph-v3-helpers'

// G-05 (plan 13.6, 11.5): an `auth_required` card for one of my OAuth connections survives
// a reload (thread state with the latest attempt), its 연결하기 opens the OAuth popup, and
// the shared receiver (`use-oauth-completion`) answers `respond: retry` with that attempt
// when the callback page posts `moldy.oauth.completed`. The OAuth start and the provider
// are route-mocked; the callback is simulated by posting the message the backend callback
// page sends. Opt in with E2E_ORG_AUTHZ=1.

interface IdRow {
  id: string
  provider?: string
}

const CREDENTIAL_ID = '33333333-3333-4333-8333-333333333333'
const INTERRUPT = {
  id: 'intr-auth-oauth',
  value: {
    type: 'auth_required',
    reason: 'expired',
    attempt: 2,
    allowed_decisions: ['respond'],
    items: [
      {
        resource_type: 'mcp_server',
        resource_name: 'Slack',
        tool_names: ['post_message'],
        credential_id: CREDENTIAL_ID,
        fixable_by: 'me',
        connect: { kind: 'oauth' },
      },
    ],
  },
}

test.describe('Auth card → OAuth popup → retry (G-05)', () => {
  test.skip(process.env.E2E_ORG_AUTHZ !== '1', 'Org-authz role fixtures (plan 16.5) pending')

  test('restores the card after reload and retries once OAuth completes', async ({
    page,
    request,
  }) => {
    const csrf = await loginApi(request)
    const models = (await apiGetJson(request, `${API_BASE}/api/models`)) as IdRow[]
    const model = models.find((candidate) => candidate.provider === 'e2e_scripted')
    if (!model) throw new Error('Scripted model is not seeded')
    const agent = (await apiPostJson(request, `${API_BASE}/api/agents`, csrf, {
      name: `E2E OAuth 카드 ${Date.now()}`,
      model_id: model.id,
      system_prompt: 'Post to Slack.',
    })) as IdRow
    const conversation = (await apiPostJson(
      request,
      `${API_BASE}/api/agents/${agent.id}/conversations`,
      csrf,
      { title: 'oauth card' },
    )) as IdRow
    const answers: Request[] = []
    try {
      await page.route(`**/langgraph/threads/${conversation.id}/state**`, async (route) => {
        const real = await route.fetch()
        const body = (await real.json()) as Record<string, unknown>
        const tasks = [{ id: 'run-auth', name: 'interrupted', interrupts: [INTERRUPT] }]
        await route.fulfill({ response: real, json: { ...body, tasks, interrupts: [INTERRUPT] } })
      })
      await page.route(`**/api/oauth2-credential/auth/${CREDENTIAL_ID}`, (route) =>
        route.fulfill({
          json: { authorization_url: 'https://oauth.example.test/authorize', state: 's' },
        }),
      )
      await page.route('https://oauth.example.test/**', (route) =>
        route.fulfill({ contentType: 'text/html', body: '<p>provider</p>' }),
      )
      await page.route('**/langgraph/threads/*/commands', async (route) => {
        if (commandMethod(route.request()) === 'input.respond') answers.push(route.request())
        await route.fallback()
      })

      const url = `/agents/${agent.id}/conversations/${conversation.id}`
      await page.goto(url)
      await expect(page.getByText('연결이 필요합니다')).toBeVisible({ timeout: 20_000 })
      // 1. A reload restores the same pending card (latest attempt → "still not connected").
      await page.reload()
      await expect(page.getByText('연결이 필요합니다')).toBeVisible({ timeout: 20_000 })
      await expect(page.getByText('아직 연결되지 않았어요', { exact: false })).toBeVisible()

      // 2. 연결하기 opens the OAuth popup at the provider.
      const popupPromise = page.waitForEvent('popup')
      await page.getByRole('button', { name: 'Slack 연결하기' }).click()
      const popup = await popupPromise
      await expect(page.getByText('팝업에서 연결을 마치면 다시 시도해요')).toBeVisible()

      // 3. The callback page posts the completion; the card answers retry for attempt 2.
      await page.evaluate((credentialId) => {
        window.postMessage({ type: 'moldy.oauth.completed', credentialId }, window.location.origin)
      }, CREDENTIAL_ID)
      await popup.close()
      await expect.poll(() => answers.length, { timeout: 15_000 }).toBeGreaterThan(0)
      const command = JSON.stringify(answers[0]?.postDataJSON())
      expect(command).toContain('"retry"')
      expect(command).toContain('"attempt":2')
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${agent.id}`, csrf)
    }
  })
})
