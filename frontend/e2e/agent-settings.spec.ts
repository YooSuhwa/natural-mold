import { test, expect } from './fixtures'
import type { APIRequestContext, Locator, Page, TestInfo } from '@playwright/test'

// Real agent-settings journeys against the live backend (no LLM needed):
// edit the system prompt and attach a sub-agent, then verify each persisted
// via the API. The settings page uses a single-save draft model — all edits
// commit on one top-right "저장" click (PATCH /api/agents/{id}).
const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'
const RUNTIME_POLICY_CAPTURE_VIEWPORTS = [375, 768, 1280] as const

async function captureRuntimePolicySettings(
  page: Page,
  testInfo: TestInfo,
  state: string,
  section: Locator,
  evidence: Locator,
): Promise<void> {
  if (testInfo.project.name !== 'scripted-capture') return

  for (const width of RUNTIME_POLICY_CAPTURE_VIEWPORTS) {
    await page.setViewportSize({ width, height: 960 })
    await page.getByRole('tab', { name: '설정', exact: true }).scrollIntoViewIfNeeded()
    await section.evaluate((element) =>
      element.scrollIntoView({ block: 'center', inline: 'nearest' }),
    )
    await expect(section).toBeInViewport()
    await expect(evidence).toBeInViewport()
    await page.screenshot({
      path: testInfo.outputPath(`runtime-settings-${state}-${width}.png`),
      fullPage: false,
    })
  }
}

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  })
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
  let contextModelId = ''
  let noContextModelId = ''
  let noContextModelName = ''

  test.beforeAll(async ({ request }) => {
    csrf = await login(request)
    const models = (await (await request.get(`${API}/api/models`)).json()) as {
      id: string
      provider: string
    }[]
    const scripted = models.find((m) => m.provider === 'e2e_scripted')
    if (!scripted) throw new Error('e2e_scripted model should be seeded')
    const unique = Date.now()
    const contextModelResponse = await request.post(`${API}/api/models`, {
      headers: csrf,
      data: {
        provider: 'e2e_scripted',
        model_name: `runtime-settings-context-${unique}`,
        display_name: `E2E Runtime Settings Context ${unique}`,
        context_window: 4096,
        cost_per_input_token: 0,
        cost_per_output_token: 0,
        supports_function_calling: true,
        input_modalities: ['text'],
        output_modalities: ['text'],
        source: 'manual',
        is_visible: true,
      },
    })
    expect(contextModelResponse.ok()).toBeTruthy()
    contextModelId = (await contextModelResponse.json()).id as string
    noContextModelName = `E2E Runtime Settings No Context ${unique}`
    const noContextModelResponse = await request.post(`${API}/api/models`, {
      headers: csrf,
      data: {
        provider: 'e2e_scripted',
        model_name: `runtime-settings-no-context-${unique}`,
        display_name: noContextModelName,
        context_window: null,
        cost_per_input_token: 0,
        cost_per_output_token: 0,
        supports_function_calling: true,
        input_modalities: ['text'],
        output_modalities: ['text'],
        source: 'manual',
        is_visible: true,
      },
    })
    expect(noContextModelResponse.ok()).toBeTruthy()
    noContextModelId = (await noContextModelResponse.json()).id as string
    const main = (await (
      await request.post(`${API}/api/agents`, {
        headers: csrf,
        data: {
          name: 'E2E Settings Agent',
          system_prompt: 'Original prompt.',
          model_id: contextModelId,
        },
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
    for (const id of [contextModelId, noContextModelId]) {
      if (id) await request.delete(`${API}/api/models/${id}`, { headers: csrf })
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

  test('runtime behavior custom, reset, reload, model constraint, and keyboard controls persist safely', async ({
    page,
    request,
  }, testInfo) => {
    await page.goto(`/agents/${agentId}/settings`)
    await page.getByRole('tab', { name: '설정', exact: true }).click()
    const runtimeSettings = page
      .getByRole('heading', { name: '실행 동작' })
      .locator('xpath=ancestor::section[1]')
    const sourceBadge = runtimeSettings.locator('[data-slot="badge"]')
    const headerSave = page.locator('header').getByRole('button', { name: '저장', exact: true })
    await expect(sourceBadge).toHaveText('권장 설정')

    const custom = runtimeSettings.getByRole('button', { name: '직접 설정', exact: true })
    await custom.focus()
    await page.keyboard.press('Enter')
    await expect(sourceBadge).toHaveText('직접 설정')

    const todoSwitch = runtimeSettings.getByRole('switch', { name: '할 일 목록 사용' })
    await todoSwitch.focus()
    await page.keyboard.press('Space')
    await expect(todoSwitch).toHaveAttribute('aria-checked', 'false')
    await runtimeSettings.getByRole('button', { name: '검토만', exact: true }).click()
    await runtimeSettings.getByRole('button', { name: '균형', exact: true }).click()
    await expect(runtimeSettings).toContainText(
      '이미 실행된 대화에는 영향을 주지 않습니다. 아직 실행하지 않은 대화와 새 대화에는 변경된 설정이 적용됩니다.',
    )

    await page.getByRole('tab', { name: '비주얼', exact: true }).click()
    await page.getByRole('tab', { name: '폼', exact: true }).click()
    await expect(todoSwitch).toHaveAttribute('aria-checked', 'false')
    await expect(
      runtimeSettings.getByRole('button', { name: '검토만', exact: true }),
    ).toHaveAttribute('aria-pressed', 'true')

    await headerSave.click()
    const expectedCustomPolicy = {
      version: 1,
      filesystem: { mode: 'inspect' },
      todo: { enabled: false },
      summarization: { mode: 'preset', preset: 'balanced_context_v1' },
    }
    await expect
      .poll(async () => (await getAgent(request, agentId)).runtime_policy, { timeout: 15_000 })
      .toEqual(expectedCustomPolicy)

    await page.reload()
    await page.getByRole('tab', { name: '설정', exact: true }).click()
    await expect(sourceBadge).toHaveText('직접 설정')
    await expect(todoSwitch).toHaveAttribute('aria-checked', 'false')
    await expect(
      runtimeSettings.getByRole('button', { name: '균형', exact: true }),
    ).toHaveAttribute('aria-pressed', 'true')
    await captureRuntimePolicySettings(
      page,
      testInfo,
      'custom-reload',
      runtimeSettings,
      runtimeSettings.getByRole('button', { name: '균형', exact: true }),
    )

    await page.getByRole('tab', { name: '폼', exact: true }).click()
    await page.getByRole('button', { name: '모델 설정', exact: true }).click()
    const modelDialog = page.getByRole('dialog')
    await expect(modelDialog).toBeVisible()
    await modelDialog.getByRole('combobox').click()
    const noContextOption = page
      .locator('[data-slot="select-content"]:visible')
      .locator('[data-slot="select-item"]')
      .filter({ hasText: noContextModelName })
    await expect(noContextOption).toBeVisible({ timeout: 10_000 })
    await noContextOption.click()
    await modelDialog.getByRole('button', { name: '완료', exact: true }).click()
    await page.getByRole('tab', { name: '설정', exact: true }).click()
    await page.getByRole('tab', { name: '설정', exact: true }).click()
    const balanced = runtimeSettings.getByRole('button', { name: '균형', exact: true })
    await expect(balanced).toBeDisabled()
    await expect(runtimeSettings).toContainText(
      '현재 선택은 이 모델에서 사용할 수 없습니다. 모델을 바꾸거나 자동으로 전환하세요.',
    )
    await captureRuntimePolicySettings(
      page,
      testInfo,
      'balanced-invalid-current',
      runtimeSettings,
      runtimeSettings.getByText(
        '현재 선택은 이 모델에서 사용할 수 없습니다. 모델을 바꾸거나 자동으로 전환하세요.',
        { exact: true },
      ),
    )
    await expect(headerSave).toBeDisabled()
    await expect
      .poll(async () => (await getAgent(request, agentId)).runtime_policy, { timeout: 15_000 })
      .toEqual(expectedCustomPolicy)
    await runtimeSettings.getByRole('button', { name: '자동', exact: true }).click()
    await expect(runtimeSettings).toContainText(
      '선택한 모델의 컨텍스트 길이 정보가 있어야 균형 모드를 사용할 수 있어요.',
    )
    await expect(headerSave).toBeEnabled()

    await runtimeSettings.getByRole('button', { name: '권장 설정 사용', exact: true }).click()
    await headerSave.click()
    await expect
      .poll(async () => (await getAgent(request, agentId)).runtime_policy, { timeout: 15_000 })
      .toBeNull()
    await page.reload()
    await page.getByRole('tab', { name: '설정', exact: true }).click()
    await expect(sourceBadge).toHaveText('권장 설정')
    await captureRuntimePolicySettings(
      page,
      testInfo,
      'recommended-reset',
      runtimeSettings,
      runtimeSettings.getByRole('heading', { name: '실행 동작' }),
    )
  })

  test('manual form and visual mode retain the pending runtime behavior choice through creation', async ({
    page,
    request,
  }) => {
    const name = `E2E Runtime Manual ${Date.now()}`
    let createdAgentId: string | null = null

    try {
      await page.goto('/agents/new/manual')
      await page.getByPlaceholder('에이전트 이름').fill(name)
      await page.locator('summary').filter({ hasText: '실행 동작 고급 설정' }).click()
      const runtimeSettings = page
        .getByRole('heading', { name: '실행 동작' })
        .locator('xpath=ancestor::section[1]')
      await runtimeSettings.getByRole('button', { name: '직접 설정', exact: true }).click()
      await runtimeSettings.getByRole('button', { name: '검토만', exact: true }).click()

      await page.getByRole('tab', { name: '비주얼', exact: true }).click()
      await page.getByRole('tab', { name: '폼', exact: true }).click()
      await page.locator('summary').filter({ hasText: '실행 동작 고급 설정' }).click()
      await expect(
        runtimeSettings.getByRole('button', { name: '검토만', exact: true }),
      ).toHaveAttribute('aria-pressed', 'true')

      await page.getByRole('button', { name: '저장', exact: true }).click()
      await expect(page).toHaveURL(/\/agents\/[^/]+\/settings$/)
      const match = page.url().match(/\/agents\/([^/]+)\/settings$/)
      if (!match?.[1])
        throw new Error('manual creation did not navigate to the agent settings route')
      createdAgentId = match[1]
      const persistedAgentId = createdAgentId
      await expect
        .poll(async () => (await getAgent(request, persistedAgentId)).runtime_policy, {
          timeout: 15_000,
        })
        .toEqual({
          version: 1,
          filesystem: { mode: 'inspect' },
          todo: { enabled: true },
          summarization: { mode: 'auto' },
        })
    } finally {
      if (createdAgentId) {
        await request.delete(`${API}/api/agents/${createdAgentId}`, { headers: csrf })
      }
    }
  })

  test('renders a generic inaccessible-agent state without runtime controls', async ({ page }) => {
    await page.goto('/agents/00000000-0000-4000-8000-000000000000/settings')
    await expect(
      page.getByText('에이전트를 찾을 수 없거나 접근 권한이 없습니다.', { exact: true }),
    ).toBeVisible()
    await expect(page.getByRole('textbox', { name: '에이전트 이름' })).toHaveCount(0)
    await expect(page.getByRole('heading', { name: '실행 동작' })).toHaveCount(0)
  })

  test('attaching a sub-agent and saving persists the delegation link', async ({
    page,
    request,
  }) => {
    await page.goto(`/agents/${agentId}/settings`)
    await page.getByRole('button', { name: '서브에이전트 관리' }).click()

    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await dialog.getByPlaceholder('에이전트 검색...').fill(childName)
    // Add the child from the "available" column (per-row add action).
    await dialog
      .getByRole('button', { name: new RegExp(`(추가|${childName})`) })
      .first()
      .click()
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
