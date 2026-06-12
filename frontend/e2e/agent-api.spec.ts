import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

// Agent API deployment (/settings/agent-api), real backend, no LLM needed:
// deploy a fixed-identity agent, issue a server API key through the create
// dialog (cleartext shown once), then revoke it — each step verified via the
// /api/agent-api endpoints. Only fixed-identity agents are deployable
// (AGENT_API_FIXED_IDENTITY_REQUIRED), so the agent is seeded with
// identity_mode: 'fixed'.
const API = process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

type ApiKey = { id: string; name: string; revoked_at: string | null }

async function listKeys(request: APIRequestContext): Promise<ApiKey[]> {
  const res = await request.get(`${API}/api/agent-api/keys`)
  expect(res.ok()).toBeTruthy()
  return (await res.json()) as ApiKey[]
}

test.describe('Agent API deployment & keys', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let csrf: Record<string, string>
  let agentId: string
  const agentName = `E2E API Agent ${Date.now()}`
  const keyName = `E2E API Key ${Date.now()}`

  test.beforeAll(async ({ request }) => {
    csrf = await login(request)
    const models = (await (await request.get(`${API}/api/models`)).json()) as {
      id: string
      provider: string
    }[]
    const scripted = models.find((m) => m.provider === 'e2e_scripted')!
    // API deployment requires a fixed-identity agent; the deploy flow itself
    // never runs the model, so the keyless scripted model is fine.
    const agent = (await (
      await request.post(`${API}/api/agents`, {
        headers: csrf,
        data: {
          name: agentName,
          system_prompt: 'x',
          model_id: scripted.id,
          identity_mode: 'fixed',
        },
      })
    ).json()) as { id: string }
    agentId = agent.id
  })

  test.afterAll(async ({ request }) => {
    // Best-effort: revoke any leftover active key, then delete the agent.
    for (const key of await listKeys(request)) {
      if (key.name === keyName && !key.revoked_at) {
        await request.post(`${API}/api/agent-api/keys/${key.id}/revoke`, { headers: csrf })
      }
    }
    if (agentId) await request.delete(`${API}/api/agents/${agentId}`, { headers: csrf })
  })

  test('deploys a fixed-identity agent, issues an API key, then revokes it', async ({
    page,
    request,
  }) => {
    test.setTimeout(60_000)
    await page.goto('/settings/agent-api')

    // 1. The fixed-identity agent shows as a ready candidate; deploy it.
    //    Generous timeout: under parallel load the backend's fixed 4-conn
    //    checkpointer pool serializes, slowing unrelated list queries.
    const candidate = page.locator('.moldy-card').filter({ hasText: agentName })
    await expect(candidate).toBeVisible({ timeout: 20_000 })
    await candidate.getByRole('button', { name: '배포' }).click()
    await expect(candidate.getByText('배포됨')).toBeVisible({ timeout: 15_000 })

    // 2. With a deployment present, the create-key action enables.
    const createKeyBtn = page.getByRole('button', { name: 'API 키', exact: true })
    await expect(createKeyBtn).toBeEnabled({ timeout: 15_000 })
    await createKeyBtn.click()

    // 3. Fill the create dialog (default scopes invoke+stream), scope to all
    //    deployments, and create.
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.locator('input').first().fill(keyName)
    await dialog.locator('label').filter({ hasText: '배포된 모든 에이전트' }).getByRole('checkbox').click()
    await dialog.getByRole('button', { name: '만들기' }).click()

    // 4. The one-time secret is revealed; capture it and acknowledge.
    const created = page.getByRole('dialog').filter({ hasText: 'API 키가 생성되었습니다' })
    await expect(created).toBeVisible({ timeout: 15_000 })
    const secret = (await created.locator('code').innerText()).trim()
    expect(secret.length).toBeGreaterThan(10)
    await created.getByRole('button', { name: '완료' }).click()

    // 5. The new key renders as active and persists via the API.
    const keyRow = page.locator('.moldy-card').filter({ hasText: keyName })
    await expect(keyRow.getByText('활성')).toBeVisible({ timeout: 15_000 })
    await expect
      .poll(async () => {
        const key = (await listKeys(request)).find((k) => k.name === keyName)
        return key && !key.revoked_at ? 'active' : 'missing'
      }, { timeout: 15_000 })
      .toBe('active')

    // 6. Revoke it through the UI; the badge flips and the API reflects it.
    await keyRow.getByRole('button', { name: 'API 키 폐기' }).click()
    await expect(keyRow.getByText('폐기됨')).toBeVisible({ timeout: 15_000 })
    await expect
      .poll(async () => {
        const key = (await listKeys(request)).find((k) => k.name === keyName)
        return key?.revoked_at ? 'revoked' : 'active'
      }, { timeout: 15_000 })
      .toBe('revoked')
  })
})
