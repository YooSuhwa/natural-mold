import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

// Real agent-settings journeys against the live backend (no LLM needed):
// edit the system prompt and attach a sub-agent, then verify each persisted
// via the API. The settings page uses a single-save draft model — all edits
// commit on one top-right "저장" click (PATCH /api/agents/{id}).
const API = process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, { data: { email: EMAIL, password: PASSWORD } })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

async function getAgent(request: APIRequestContext, id: string): Promise<Record<string, unknown>> {
  const res = await request.get(`${API}/api/agents/${id}`)
  expect(res.ok()).toBeTruthy()
  return (await res.json()) as Record<string, unknown>
}

test.describe('Agent settings — edit & attach', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let csrf: Record<string, string>
  let agentId: string
  let childId: string
  let childName: string
  let skillId: string
  let skillName: string
  let toolId: string
  let toolName: string

  test.beforeAll(async ({ request }) => {
    csrf = await login(request)
    const models = (await (await request.get(`${API}/api/models`)).json()) as {
      id: string
      provider: string
    }[]
    const scripted = models.find((m) => m.provider === 'e2e_scripted')!
    const main = (await (
      await request.post(`${API}/api/agents`, {
        headers: csrf,
        data: { name: 'E2E Settings Agent', system_prompt: 'Original prompt.', model_id: scripted.id },
      })
    ).json()) as { id: string }
    agentId = main.id
    childName = `E2E Child ${Date.now()}`
    const child = (await (
      await request.post(`${API}/api/agents`, {
        headers: csrf,
        data: { name: childName, system_prompt: 'I am a sub-agent.', model_id: scripted.id },
      })
    ).json()) as { id: string }
    childId = child.id

    skillName = `E2E Attach Skill ${Date.now()}`
    const skill = (await (
      await request.post(`${API}/api/skills`, {
        headers: csrf,
        data: {
          name: skillName,
          content: `---\nname: ${skillName}\ndescription: E2E attach skill.\n---\nDo the task.`,
        },
      })
    ).json()) as { id: string }
    skillId = skill.id

    toolName = `E2E Attach Tool ${Date.now()}`
    const tool = (await (
      await request.post(`${API}/api/tools`, {
        headers: csrf,
        // tavily_search needs no per-tool credential (hosted key).
        data: { definition_key: 'tavily_search', name: toolName },
      })
    ).json()) as { id: string }
    toolId = tool.id
  })

  test.afterAll(async ({ request }) => {
    for (const id of [agentId, childId]) {
      if (id) await request.delete(`${API}/api/agents/${id}`, { headers: csrf })
    }
    if (skillId) await request.delete(`${API}/api/skills/${skillId}`, { headers: csrf })
    if (toolId) await request.delete(`${API}/api/tools/${toolId}`, { headers: csrf })
  })

  test('editing the system prompt and saving persists it', async ({ page, request }) => {
    await page.goto(`/agents/${agentId}/settings`)
    const textarea = page.locator('textarea[placeholder="에이전트 지침을 입력하세요"]')
    await expect(textarea).toBeVisible()

    const newPrompt = `Updated by E2E ${Date.now()}`
    await textarea.fill(newPrompt)
    await page.getByRole('button', { name: '저장', exact: true }).click()

    await expect
      .poll(async () => (await getAgent(request, agentId)).system_prompt, { timeout: 15_000 })
      .toBe(newPrompt)
  })

  test('attaching a sub-agent and saving persists the delegation link', async ({ page, request }) => {
    await page.goto(`/agents/${agentId}/settings`)
    await page.getByRole('button', { name: '서브에이전트 관리' }).click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByPlaceholder('에이전트 검색...').fill(childName)
    // Add the child from the "available" column (per-row add action).
    await dialog.getByRole('button', { name: new RegExp(`(추가|${childName})`) }).first().click()
    await dialog.getByRole('button', { name: '닫기' }).click()

    await page.getByRole('button', { name: '저장', exact: true }).click()

    await expect
      .poll(
        async () => {
          const agent = await getAgent(request, agentId)
          const subs = (agent.sub_agents ?? []) as Array<{ id?: string } | string>
          return subs.map((s) => (typeof s === 'string' ? s : s.id)).filter(Boolean)
        },
        { timeout: 15_000 },
      )
      .toContain(childId)
  })

  test('attaching a skill and saving persists it', async ({ page, request }) => {
    await page.goto(`/agents/${agentId}/settings`)
    await page.getByRole('button', { name: '추가', exact: true }).first().click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByRole('tab', { name: 'Skills' }).click()
    await dialog.getByRole('button', { name: `${skillName} 추가` }).click()
    await dialog.getByRole('button', { name: '닫기' }).click()

    await page.getByRole('button', { name: '저장', exact: true }).click()

    await expect
      .poll(
        async () => {
          const agent = await getAgent(request, agentId)
          const skills = (agent.skills ?? []) as Array<{ id?: string } | string>
          return skills.map((s) => (typeof s === 'string' ? s : s.id)).filter(Boolean)
        },
        { timeout: 15_000 },
      )
      .toContain(skillId)
  })

  test('attaching a tool and saving persists it', async ({ page, request }) => {
    await page.goto(`/agents/${agentId}/settings`)
    await page.getByRole('button', { name: '추가', exact: true }).first().click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByRole('tab', { name: 'My Tools' }).click()
    await dialog.getByRole('button', { name: `${toolName} 추가` }).click()
    await dialog.getByRole('button', { name: '닫기' }).click()

    await page.getByRole('button', { name: '저장', exact: true }).click()

    await expect
      .poll(
        async () => {
          const agent = await getAgent(request, agentId)
          const tools = (agent.tools ?? []) as Array<{ id?: string } | string>
          return tools.map((t) => (typeof t === 'string' ? t : t.id)).filter(Boolean)
        },
        { timeout: 15_000 },
      )
      .toContain(toolId)
  })
})
