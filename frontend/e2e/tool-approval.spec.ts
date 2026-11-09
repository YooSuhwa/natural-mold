import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

// approval-modes-plan: tool approval end to end on the keyless scripted model. The company
// opens code execution to agents ("기본 승인, 에이전트가 풀 수 있음"); execute_in_skill then
// still asks by default, offers "이 대화 동안 묻지 않기" on its card, and after that runs
// without a card in the same conversation. The agent's settings panel shows the effective
// table, and switching the chat mode to "자동" makes chat calls run unasked.
const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'
const SLOW = { timeout: 30_000 }
const ASK = 'mcp 도구 사용 승인 HITL'

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

async function getJson<T>(request: APIRequestContext, url: string): Promise<T> {
  const res = await request.get(url)
  expect(res.ok(), `GET ${url} → ${res.status()}`).toBeTruthy()
  return (await res.json()) as T
}

async function setCodeExecution(
  request: APIRequestContext,
  csrf: Record<string, string>,
  action: 'approval' | 'approval_relaxable',
): Promise<void> {
  const orgs = await getJson<{ tenant_id: string; is_active: boolean }[]>(
    request,
    `${API}/api/me/organizations`,
  )
  const tenantId = (orgs.find((row) => row.is_active) ?? orgs[0]).tenant_id
  const url = `${API}/api/tenants/${tenantId}/guardrails`
  const current = await getJson<{
    mode: string
    default_template: string | null
    locked_rules: string[]
    tool_risk: Record<string, string>
    updated_at: string
  }>(request, url)
  const res = await request.put(url, {
    headers: { ...csrf, 'If-Match': `"${current.updated_at.replace(/Z$/, '+00:00')}"` },
    data: {
      mode: current.mode,
      default_template: current.default_template,
      locked_rules: current.locked_rules,
      tool_risk: { ...current.tool_risk, code_execution: action },
    },
  })
  expect(res.ok(), `PUT policy → ${res.status()} ${await res.text()}`).toBeTruthy()
}

/** How many execute_in_skill results the conversation holds (each is one call that ran). */
async function toolResults(request: APIRequestContext, conversationId: string): Promise<number> {
  const body = await getJson<{ messages: { role: string; tool_call_id: string | null }[] }>(
    request,
    `${API}/api/conversations/${conversationId}/messages`,
  )
  return body.messages.filter((message) => message.role === 'tool' && message.tool_call_id).length
}

async function activeRunIdle(request: APIRequestContext, conversationId: string) {
  await expect
    .poll(
      async () =>
        (
          await getJson<{ status: string } | null>(
            request,
            `${API}/api/conversations/${conversationId}/runs/active`,
          )
        )?.status ?? 'idle',
      SLOW,
    )
    .toBe('idle')
}

test.describe('tool approval modes', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.describe.configure({ mode: 'serial' })
  test.setTimeout(150_000)

  let csrf: Record<string, string>
  let agentId = ''

  test.beforeAll(async ({ request }) => {
    csrf = await login(request)
    await setCodeExecution(request, csrf, 'approval_relaxable')
    const items = await getJson<
      { id: string; slug: string; installation: { installed_resource_id?: string | null } }[]
    >(request, `${API}/api/marketplace/items?resource_type=skill&source_kind=system_seed&limit=200`)
    const docx = items.find((item) => item.slug === 'docx-document')
    if (!docx) throw new Error('docx-document skill should be seeded')
    const install = (await (
      await request.post(`${API}/api/marketplace/items/${docx.id}/install`, {
        headers: csrf,
        data: { install_mode: 'overwrite_existing' },
      })
    ).json()) as { installed_skill_id?: string | null }
    const skillId = install.installed_skill_id ?? docx.installation.installed_resource_id
    const models = await getJson<{ id: string; provider: string; model_name: string }[]>(
      request,
      `${API}/api/models`,
    )
    const scripted = models.find(
      (m) => m.provider === 'e2e_scripted' && m.model_name === 'document-artifact-scripted',
    )
    if (!scripted) throw new Error('document-artifact-scripted model should be seeded')
    const agent = (await (
      await request.post(`${API}/api/agents`, {
        headers: csrf,
        data: {
          name: `E2E 도구 승인 ${Date.now()}`,
          system_prompt: 'Use execute_in_skill exactly when the scripted model requests it.',
          model_id: scripted.id,
          skill_ids: [skillId],
        },
      })
    ).json()) as { id: string }
    agentId = agent.id
  })

  test.afterAll(async ({ request }) => {
    csrf = await login(request)
    await setCodeExecution(request, csrf, 'approval')
    if (agentId) await request.delete(`${API}/api/agents/${agentId}`, { headers: csrf })
  })

  test('a conversation grant lifts the card for later calls', async ({ page, request }) => {
    csrf = await login(request) // each test has its own request context (and CSRF cookie)
    const created = await request.post(`${API}/api/agents/${agentId}/conversations`, {
      headers: csrf,
      data: { title: '대화 허용 E2E' },
    })
    expect(created.ok(), `create conversation → ${created.status()}`).toBeTruthy()
    const conversation = (await created.json()) as { id: string }
    const view = await getJson<{ tools: { name: string; grantable: boolean }[] }>(
      request,
      `${API}/api/agents/${agentId}/tool-approval`,
    )
    expect(view.tools.find((tool) => tool.name === 'execute_in_skill')).toMatchObject({
      grantable: true,
    })
    await page.goto(`/agents/${agentId}/conversations/${conversation.id}`)
    const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
    await expect(composer).toBeVisible(SLOW)
    await composer.fill(ASK)
    await composer.press('Enter')

    // The default still asks; the card offers the grant.
    const grant = page.getByRole('button', { name: '이 대화 동안 이 도구는 묻지 않기' })
    await expect(grant).toBeVisible(SLOW)
    await grant.click()
    const chips = page.getByRole('region', { name: '이 대화에서 허용한 도구' })
    await expect(chips).toContainText('execute_in_skill', SLOW)
    await page.getByTestId('approval-approve-button').last().click()
    await expect(page.getByText('승인됨').last()).toBeVisible(SLOW)
    await expect.poll(() => toolResults(request, conversation.id), SLOW).toBe(1)
    await activeRunIdle(request, conversation.id)

    // The next call in this conversation runs without a card and without a pending request.
    await composer.fill(ASK)
    await composer.press('Enter')
    await expect.poll(() => toolResults(request, conversation.id), SLOW).toBe(2)
    await activeRunIdle(request, conversation.id)
    const pending = await getJson<{ items: { conversation_id: string | null }[] }>(
      request,
      `${API}/api/approvals?status=pending`,
    )
    expect(pending.items.filter((item) => item.conversation_id === conversation.id)).toEqual([])
    await expect(page.getByTestId('approval-approve-button')).toHaveCount(0)

    // Revoking puts the card back.
    await chips.getByRole('button', { name: 'execute_in_skill 허용 철회' }).click()
    await expect(chips).toHaveCount(0, SLOW)
  })

  test('the settings panel switches chat to auto and shows the effect', async ({
    page,
    request,
  }) => {
    await page.goto(`/agents/${agentId}/settings`)
    await page.getByRole('tab', { name: '설정', exact: true }).click()
    const panel = page.locator('section[aria-labelledby="tool-approval-title"]')
    await expect(panel).toBeVisible(SLOW)
    const effective = panel.getByRole('list', { name: 'execute_in_skill 실제 적용' })
    await expect(effective).toContainText('채팅 · 묻기', SLOW)
    await panel.getByLabel('채팅').selectOption('auto')
    await page.getByRole('button', { name: '저장', exact: true }).first().click()
    await expect(effective).toContainText('채팅 · 자동', SLOW)
    await expect(effective).toContainText('워크플로 · 묻기')

    const view = await getJson<{ settings: { paths?: Record<string, string> } | null }>(
      request,
      `${API}/api/agents/${agentId}/tool-approval`,
    )
    expect(view.settings?.paths?.chat).toBe('auto')
  })
})
