import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

// HITL tool approval — reject path. execute_in_skill carries a default
// interrupt policy (tool risk metadata), so a scripted document run pauses on
// an approval card before the tool executes. The approve path is covered by
// document-artifact-viewers; this asserts the reject path: rejecting skips the
// tool, so no artifact is produced. Setup mirrors the document spec (install a
// system-seed skill, attach it, drive the keyless scripted model via E2E_DOCX).
const API = process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

async function getJson<T>(request: APIRequestContext, url: string): Promise<T> {
  const res = await request.get(url)
  expect(res.ok(), `GET ${url} → ${res.status()}`).toBeTruthy()
  return (await res.json()) as T
}

test.describe('HITL tool approval — reject', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let csrf: Record<string, string>
  let agentId: string
  let conversationId: string

  test.beforeAll(async ({ request }) => {
    csrf = await login(request)

    // Install the seeded docx skill so execute_in_skill is available.
    const items = await getJson<{ id: string; slug: string; installation: { installed_resource_id?: string | null } }[]>(
      request,
      `${API}/api/marketplace/items?resource_type=skill&source_kind=system_seed&limit=200`,
    )
    const docx = items.find((i) => i.slug === 'docx-document')
    expect(docx, 'docx-document skill should be seeded').toBeTruthy()
    const install = (await (
      await request.post(`${API}/api/marketplace/items/${docx!.id}/install`, {
        headers: csrf,
        data: { install_mode: 'overwrite_existing' },
      })
    ).json()) as { installed_skill_id?: string | null }
    const skillId = install.installed_skill_id ?? docx!.installation.installed_resource_id
    expect(skillId).toBeTruthy()

    const models = await getJson<{ id: string; provider: string; model_name: string }[]>(
      request,
      `${API}/api/models`,
    )
    const scripted = models.find(
      (m) => m.provider === 'e2e_scripted' && m.model_name === 'document-artifact-scripted',
    )!

    const agent = (await (
      await request.post(`${API}/api/agents`, {
        headers: csrf,
        data: {
          name: `E2E HITL Reject Agent ${Date.now()}`,
          system_prompt: 'Use execute_in_skill exactly when the scripted model requests it.',
          model_id: scripted.id,
          skill_ids: [skillId],
        },
      })
    ).json()) as { id: string }
    agentId = agent.id
    const conv = (await (
      await request.post(`${API}/api/agents/${agentId}/conversations`, {
        headers: csrf,
        data: { title: 'HITL Reject E2E' },
      })
    ).json()) as { id: string }
    conversationId = conv.id
  })

  test.afterAll(async ({ request }) => {
    if (agentId) await request.delete(`${API}/api/agents/${agentId}`, { headers: csrf })
  })

  test('rejecting an execute_in_skill approval skips the tool', async ({ page, request }) => {
    test.setTimeout(120_000)
    await page.goto(`/agents/${agentId}/conversations/${conversationId}`)

    const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
    await expect(composer).toBeVisible()
    await composer.fill('E2E_DOCX please generate the document.')
    await composer.press('Enter')

    // 1. The tool call pauses on an approval card before executing.
    await expect(page.getByText('승인이 필요합니다').last()).toBeVisible({ timeout: 30_000 })

    // 2. Reject → confirm.
    await page.getByRole('button', { name: '거부', exact: true }).last().click()
    await page.getByRole('button', { name: '거부 확인' }).last().click()

    // 3. The card resolves to the rejected state.
    await expect(page.getByText('거부됨').last()).toBeVisible({ timeout: 30_000 })

    // 4. The tool never ran → no document artifact was produced.
    await expect
      .poll(
        async () =>
          (
            await getJson<{ status: string } | null>(
              request,
              `${API}/api/conversations/${conversationId}/runs/active`,
            )
          )?.status ?? 'idle',
        { timeout: 30_000 },
      )
      .toBe('idle')
    const artifacts = await getJson<{ display_name: string }[]>(
      request,
      `${API}/api/conversations/${conversationId}/artifacts`,
    )
    expect(artifacts.map((a) => a.display_name)).not.toContain('moldy-docx-demo.docx')
  })
})
