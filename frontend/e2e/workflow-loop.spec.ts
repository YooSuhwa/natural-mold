import { test, expect } from './fixtures'
import type { APIRequestContext, Page } from '@playwright/test'

import { isSeededScriptedModel } from './helpers/scripted-model'

// workflow-loop-plan L-d: the "통과할 때까지 다시 쓰기" template end to end on the keyless
// scripted model and the job worker. The scripted reviewer answers ``passed`` = true only
// when E2E_WORKFLOW_YES is in its prompt (the topic is), so a plain topic fails every round
// and the run takes the limit exit to the manager after three rounds.
const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'
const SLOW = { timeout: 30_000 }

type Step = {
  node_id: string
  round: number
  branch: string | null
  output: Record<string, unknown> | null
}
type Run = { id: string; status: string; steps: Step[] }

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

function summary(page: Page) {
  return page.locator('section.moldy-panel').first()
}

function statusChip(page: Page, label: string) {
  return summary(page).getByText(label, { exact: true })
}

test.describe('workflow loop — rewrite until the review passes', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.describe.configure({ mode: 'serial' })
  test.setTimeout(180_000)

  let workflowId = ''
  const agents = { writer: '', reviewer: '' }

  test.beforeAll(async ({ request }) => {
    const csrf = await login(request)
    const models = await getJson<unknown[]>(request, `${API}/api/models`)
    const scripted = models.find(isSeededScriptedModel)
    if (!scripted) throw new Error('the seeded scripted model should exist')
    const stamp = Date.now()
    for (const [key, name] of [
      ['writer', `E2E 초안 작성 ${stamp}`],
      ['reviewer', `E2E 품질 검사 ${stamp}`],
    ] as const) {
      const res = await request.post(`${API}/api/agents`, {
        headers: csrf,
        data: { name, system_prompt: '글을 다룹니다.', model_id: scripted.id },
      })
      expect(res.ok(), `create agent → ${res.status()}`).toBeTruthy()
      agents[key] = ((await res.json()) as { id: string }).id
    }
  })

  test('three failed reviews take the limit exit, and the manager accepts the last draft', async ({
    page,
    request,
  }) => {
    await page.goto('/workflows')
    await page.getByRole('button', { name: '새 워크플로' }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByLabel('이름').fill(`E2E 다시 쓰기 ${Date.now()}`)
    await dialog.getByRole('radio', { name: '통과할 때까지 다시 쓰기' }).check()
    await dialog.getByLabel('초안을 쓸 에이전트').selectOption(agents.writer)
    await dialog.getByLabel('검사할 에이전트').selectOption(agents.reviewer)
    await dialog.getByRole('button', { name: '만들기' }).click()
    await expect(page).toHaveURL(/\/workflows\/[0-9a-f-]{36}$/, SLOW)
    workflowId = page.url().split('/').pop() ?? ''

    // The loop is on the canvas and nothing blocks publishing.
    await expect(page.locator('.react-flow__node')).toHaveCount(8, SLOW)
    await expect(page.getByText('↺ 최대 3회')).toBeVisible()
    await expect(page.getByText('게시할 수 있어요').first()).toBeVisible(SLOW)
    await page.getByRole('button', { name: '게시', exact: true }).click()
    await expect(page.getByText('v1으로 게시했어요')).toBeVisible(SLOW)

    await page.getByRole('button', { name: '시험 실행' }).click()
    await page
      .getByRole('dialog')
      .getByLabel(/주제와 요구사항/)
      .fill('분기 매출 보고서 요약')
    await page.getByRole('dialog').getByRole('button', { name: '실행', exact: true }).click()
    await expect(page).toHaveURL(/\/runs\/[0-9a-f-]{36}$/, SLOW)
    const runId = page.url().split('/').pop() ?? ''

    // Three rounds, then the limit exit to the manager's check.
    await expect(statusChip(page, '승인 대기')).toBeVisible(SLOW)
    const timeline = page.getByRole('list', { name: '단계' })
    await expect(timeline.getByText('3회차')).toHaveCount(3)
    await expect(timeline.getByText('한도 초과')).toBeVisible()
    await expect(
      page.getByRole('heading', { name: /3번 고쳐도 검사를 통과하지 못했어요/ }),
    ).toBeVisible(SLOW)
    const run = await getJson<Run>(request, `${API}/api/workflow-runs/${runId}`)
    expect(run.steps.map((step) => [step.node_id, step.round, step.branch])).toEqual([
      ['start', 1, null],
      ['draft', 1, null],
      ['review', 1, null],
      ['passed', 1, 'false'],
      ['draft', 2, null],
      ['review', 2, null],
      ['passed', 2, 'false'],
      ['draft', 3, null],
      ['review', 3, null],
      ['passed', 3, 'limit'],
      ['escalate', 1, null],
    ])
    expect(run.steps[9].output).toMatchObject({ limit_reached: true, reason: 'max_rounds' })

    await page.getByRole('button', { name: '승인 후 계속' }).click()
    await expect(statusChip(page, '완료')).toBeVisible(SLOW)
    await expect
      .poll(async () => {
        const page = await getJson<{ items: { target_id: string | null; metadata: unknown }[] }>(
          request,
          `${API}/api/audit-events?action=workflow.loop_limit&limit=100`,
        )
        return page.items.find((item) => item.target_id === runId)?.metadata
      }, SLOW)
      .toMatchObject({ decision_node_id: 'passed', rounds: 3, reason: 'max_rounds' })
  })

  test('a review that passes ends after one round', async ({ page, request }) => {
    const csrf = await login(request)
    const res = await request.post(`${API}/api/workflows/${workflowId}/runs`, {
      headers: csrf,
      data: { inputs: { topic: 'E2E_WORKFLOW_YES 분기 매출 보고서 요약' } },
    })
    expect(res.ok()).toBeTruthy()
    const runId = ((await res.json()) as { id: string }).id
    await expect
      .poll(
        async () => (await getJson<Run>(request, `${API}/api/workflow-runs/${runId}`)).status,
        SLOW,
      )
      .toBe('completed')
    await page.goto(`/workflows/${workflowId}/runs/${runId}`)
    await expect(statusChip(page, '완료')).toBeVisible(SLOW)
    const timeline = page.getByRole('list', { name: '단계' })
    await expect(timeline.getByText('2회차')).toHaveCount(0)
    await expect(timeline.getByRole('button').filter({ hasText: '완성본 출력' })).toBeVisible()
  })
})
