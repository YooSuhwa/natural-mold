import { test, expect } from './fixtures'
import type { APIRequestContext, Page } from '@playwright/test'

import { isSeededScriptedModel } from './helpers/scripted-model'

// 순번 23 W5: AI editing on the editor's AI tab, end to end on the keyless scripted model
// (the E2E system ``text_primary``). On an empty draft the scripted model proposes
// start → research agent → output; "E2E_WORKFLOW_ASK" makes it ask back
// (backend/app/agent_runtime/e2e_scripted_model.py ``workflow_edit_args``).
const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'
const SLOW = { timeout: 30_000 }

type Draft = { draft_revision: number; draft_definition: { nodes: { id: string }[] } }

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

async function draftOf(request: APIRequestContext, workflowId: string): Promise<Draft> {
  const res = await request.get(`${API}/api/workflows/${workflowId}`)
  expect(res.ok()).toBeTruthy()
  return (await res.json()) as Draft
}

async function openBlankWorkflow(page: Page): Promise<string> {
  await page.goto('/workflows')
  await page.getByRole('button', { name: '새 워크플로' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('이름').fill(`E2E AI 편집 ${Date.now()}`)
  await dialog.getByRole('button', { name: '만들기' }).click()
  await expect(page).toHaveURL(/\/workflows\/[0-9a-f-]{36}$/, SLOW)
  await expect(page.locator('.react-flow__node')).toHaveCount(1, SLOW)
  await page.getByRole('tab', { name: 'AI' }).click()
  return page.url().split('/').pop() ?? ''
}

async function ask(page: Page, text: string) {
  await page.getByLabel('AI에게 요청').fill(text)
  await page.getByRole('button', { name: '보내기' }).click()
}

test.describe('workflow AI editing', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.describe.configure({ mode: 'serial' })

  test.beforeAll(async ({ request }) => {
    const csrf = await login(request)
    const models = await (await request.get(`${API}/api/models`)).json()
    const scripted = (models as unknown[]).find(isSeededScriptedModel)
    if (!scripted) throw new Error('the seeded scripted model should exist')
    const res = await request.post(`${API}/api/agents`, {
      headers: csrf,
      data: {
        name: `E2E 자료 조사 ${Date.now()}`,
        system_prompt: '주제를 조사합니다.',
        model_id: scripted.id,
      },
    })
    expect(res.ok()).toBeTruthy()
  })

  test('a proposal shows its plan and diff, discarding keeps the draft, applying can be undone', async ({
    page,
    request,
  }) => {
    const workflowId = await openBlankWorkflow(page)
    const nodes = page.locator('.react-flow__node')
    const before = await draftOf(request, workflowId)

    // A large change shows the plan first; nothing on the canvas changes yet.
    await ask(page, '주제를 받아 조사하고 요약을 결과로 내보내 줘')
    const plan = page.getByRole('region', { name: '이렇게 바꿀게요' })
    await expect(plan).toContainText('조사 단계 추가', SLOW)
    await expect(nodes).toHaveCount(1)

    await plan.getByRole('button', { name: '바뀌는 점 보기' }).click()
    const review = page.getByRole('region', { name: 'AI 제안 검토' })
    await expect(review).toContainText('추가 2')
    await expect(nodes).toHaveCount(3)

    await review.getByRole('button', { name: '버리기' }).click()
    await expect(nodes).toHaveCount(1)
    const kept = await draftOf(request, workflowId)
    expect(kept.draft_revision).toBe(before.draft_revision)
    expect(kept.draft_definition.nodes.map((node) => node.id)).toEqual(['start'])

    // Ask again and apply: an ordinary draft save, checked like any other.
    await ask(page, '주제를 받아 조사하고 요약을 결과로 내보내 줘')
    await page.getByRole('button', { name: '바뀌는 점 보기' }).click()
    await page
      .getByRole('region', { name: 'AI 제안 검토' })
      .getByRole('button', { name: '모두 적용' })
      .click()
    await expect(nodes).toHaveCount(3)
    await expect(page.getByText('저장됨')).toBeVisible(SLOW)
    await expect(page.getByText('게시할 수 있어요').first()).toBeVisible(SLOW)
    await expect
      .poll(async () => (await draftOf(request, workflowId)).draft_definition.nodes.length, SLOW)
      .toBe(3)

    // The applied proposal is one edit: undo takes it back.
    await page.getByRole('button', { name: '되돌리기' }).click()
    await expect(nodes).toHaveCount(1)
    await page.getByRole('button', { name: '다시 하기' }).click()
    await expect(nodes).toHaveCount(3)

    // Re-wiring by hand: cut the research step's exit, the checks flag the dead end, wire it back.
    await page.getByRole('tab', { name: '설정' }).click()
    await nodes.filter({ hasText: '자료 조사' }).click()
    const next = page.getByLabel('끝나면')
    await expect(next).toHaveValue('done')
    await next.selectOption('')
    await expect(page.locator('.react-flow__edge')).toHaveCount(1)
    await expect(page.getByText(/확인할 것 \d+개/).first()).toBeVisible(SLOW)
    await next.selectOption('done')
    await expect(page.locator('.react-flow__edge')).toHaveCount(2)
    await expect(page.getByText('게시할 수 있어요').first()).toBeVisible(SLOW)
  })

  test('an ambiguous request is asked back without a proposal', async ({ page }) => {
    await openBlankWorkflow(page)
    await ask(page, 'E2E_WORKFLOW_ASK 흐름 하나 만들어 줘')
    await expect(
      page
        .getByRole('list', { name: 'AI와 나눈 대화' })
        .getByText('어떤 자료를 다루는 흐름인가요?'),
    ).toBeVisible(SLOW)
    await expect(page.getByRole('region', { name: '이렇게 바꿀게요' })).toHaveCount(0)
    await expect(page.locator('.react-flow__node')).toHaveCount(1)
  })
})
