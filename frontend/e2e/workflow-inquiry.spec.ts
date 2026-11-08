import { test, expect } from './fixtures'
import type { APIRequestContext, Page } from '@playwright/test'

import { isSeededScriptedModel } from './helpers/scripted-model'

// 순번 23 W4b: the real task (Q3 "고객 문의 답변 초안") end to end on the keyless scripted
// model. The workflow is designed in the screen from the template, published, run through the
// job worker, and decided on the run page; the audit trail keeps each step (plan 1장 기준 1·5).
// The scripted model answers every structured output with values from the schema; the
// E2E_WORKFLOW_YES marker in the inquiry turns ``urgent`` to true (e2e_scripted_model.py).
const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'
const URGENT = 'E2E_WORKFLOW_YES 결제가 두 번 됐어요. 오늘 안에 환불해 주세요.'
const CALM = '비밀번호를 바꾸는 방법이 궁금해요.'
const SLOW = { timeout: 30_000 }

type Run = { id: string; status: string; error_code: string | null; version_no: number | null }
type Approval = { id: string; workflow_run_id: string | null; kind: string }
type AuditEvent = {
  action: string
  target_id: string | null
  outcome: string
  reason_code: string | null
  metadata: Record<string, unknown> | null
}

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

async function runOf(request: APIRequestContext, runId: string): Promise<Run> {
  return getJson<Run>(request, `${API}/api/workflow-runs/${runId}`)
}

async function pendingApproval(request: APIRequestContext, runId: string): Promise<Approval> {
  let found: Approval | undefined
  await expect
    .poll(async () => {
      const page = await getJson<{ items: Approval[] }>(
        request,
        `${API}/api/approvals?status=pending`,
      )
      found = page.items.find((item) => item.workflow_run_id === runId)
      return found?.kind
    }, SLOW)
    .toBe('business')
  return found as Approval
}

async function auditOf(
  request: APIRequestContext,
  action: string,
  targetId: string,
): Promise<AuditEvent> {
  let found: AuditEvent | undefined
  await expect
    .poll(async () => {
      const page = await getJson<{ items: AuditEvent[] }>(
        request,
        `${API}/api/audit-events?action=${encodeURIComponent(action)}&limit=100`,
      )
      found = page.items.find((item) => item.target_id === targetId)
      return Boolean(found)
    }, SLOW)
    .toBe(true)
  return found as AuditEvent
}

/** The run page's summary panel: status chip, version, error message. */
function runStatus(page: Page) {
  return page.locator('section.moldy-panel').first()
}

/** The run's status chip, matched exactly ("완료" is also in "N 단계 완료"). */
function statusChip(page: Page, label: string) {
  return runStatus(page).getByText(label, { exact: true })
}

function step(page: Page, label: string) {
  return page.getByRole('list', { name: '단계' }).getByRole('button').filter({ hasText: label })
}

test.describe('workflow Q3 — customer inquiry reply draft', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.describe.configure({ mode: 'serial' })
  test.setTimeout(180_000)

  let workflowId = ''
  const agents = { classifier: '', writer: '' }

  test.beforeAll(async ({ request }) => {
    const csrf = await login(request)
    const models = await getJson<unknown[]>(request, `${API}/api/models`)
    const scripted = models.find(isSeededScriptedModel)
    if (!scripted) throw new Error('the seeded scripted model should exist')
    const stamp = Date.now()
    for (const [key, name] of [
      ['classifier', `E2E 문의 분류 ${stamp}`],
      ['writer', `E2E 답변 작성 ${stamp}`],
    ] as const) {
      const res = await request.post(`${API}/api/agents`, {
        headers: csrf,
        data: { name, system_prompt: '고객 문의를 처리합니다.', model_id: scripted.id },
      })
      expect(res.ok(), `create agent → ${res.status()}`).toBeTruthy()
      agents[key] = ((await res.json()) as { id: string }).id
    }
  })

  test('designs it from the template, publishes, and finishes an urgent run after approval', async ({
    page,
    request,
  }) => {
    await page.goto('/workflows')
    await page.getByRole('button', { name: '새 워크플로' }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByLabel('이름').fill(`E2E 고객 문의 ${Date.now()}`)
    await dialog.getByRole('radio', { name: /고객 문의 답변 초안/ }).check()
    await dialog.getByLabel('문의를 분류할 에이전트').selectOption(agents.classifier)
    await dialog.getByLabel('답변을 쓸 에이전트').selectOption(agents.writer)
    await dialog.getByRole('button', { name: '만들기' }).click()
    await expect(page).toHaveURL(/\/workflows\/[0-9a-f-]{36}$/, SLOW)
    workflowId = page.url().split('/').pop() ?? ''

    // All seven node kinds are on the canvas and nothing blocks publishing.
    const canvasNodes = page.locator('.react-flow__node')
    await expect(canvasNodes).toHaveCount(8, SLOW)
    await expect(page.getByText('게시할 수 있어요').first()).toBeVisible(SLOW)

    // A design change in the screen: the manager gets two hours to answer.
    await canvasNodes.filter({ hasText: '담당자 승인' }).click()
    const timeout = page.getByLabel('응답 기한(분)')
    await timeout.fill('120')
    await timeout.blur()
    await expect(page.getByText('저장됨')).toBeVisible(SLOW)

    await page.getByRole('button', { name: '게시', exact: true }).click()
    await expect(page.getByText('v1으로 게시했어요')).toBeVisible(SLOW)
    const version = await getJson<{
      definition: { nodes: { id: string; timeout_minutes?: number }[] }
    }>(request, `${API}/api/workflows/${workflowId}/versions/1`)
    expect(version.definition.nodes.find((node) => node.id === 'review')?.timeout_minutes).toBe(120)

    await page.getByRole('button', { name: '시험 실행' }).click()
    await page
      .getByRole('dialog')
      .getByLabel(/문의 내용/)
      .fill(URGENT)
    await page.getByRole('dialog').getByRole('button', { name: '실행', exact: true }).click()
    await expect(page).toHaveURL(/\/runs\/[0-9a-f-]{36}$/, SLOW)
    const runId = page.url().split('/').pop() ?? ''

    // The run waits on the manager: path chosen, version kept, approval card on the run page.
    await expect(statusChip(page, '승인 대기')).toBeVisible(SLOW)
    await expect(runStatus(page)).toContainText('v1')
    await expect(step(page, '긴급한가')).toContainText('예')
    await expect(page.getByRole('heading', { name: /긴급 문의 확인: / })).toBeVisible(SLOW)
    const approval = await pendingApproval(request, runId)

    await page.getByRole('button', { name: '승인 후 계속' }).click()
    await expect(statusChip(page, '완료')).toBeVisible(SLOW)
    await expect(step(page, '답변 초안 출력')).toBeVisible()
    const finalOutput = page
      .locator('section')
      .filter({ has: page.getByRole('heading', { name: '최종 출력' }) })
    await expect(finalOutput).toContainText('[문의 답변] ')

    const finished = await runOf(request, runId)
    expect(finished).toMatchObject({ status: 'completed', version_no: 1 })
    expect(await auditOf(request, 'workflow.create', workflowId)).toMatchObject({
      outcome: 'success',
    })
    expect(await auditOf(request, 'workflow.publish', workflowId)).toMatchObject({
      outcome: 'success',
    })
    expect(await auditOf(request, 'workflow.run_start', runId)).toMatchObject({
      outcome: 'success',
      metadata: expect.objectContaining({ workflow_id: workflowId }),
    })
    expect(await auditOf(request, 'approval.decided', approval.id)).toMatchObject({
      outcome: 'success',
      metadata: expect.objectContaining({ status: 'approved' }),
    })
    expect(await auditOf(request, 'workflow.run_finish', runId)).toMatchObject({
      outcome: 'success',
      metadata: expect.objectContaining({ workflow_id: workflowId, status: 'completed' }),
    })
  })

  test('a calm inquiry skips the approval', async ({ page, request }) => {
    const csrf = await login(request)
    const res = await request.post(`${API}/api/workflows/${workflowId}/runs`, {
      headers: csrf,
      data: { inputs: { inquiry: CALM } },
    })
    expect(res.ok()).toBeTruthy()
    const runId = ((await res.json()) as Run).id
    await expect.poll(async () => (await runOf(request, runId)).status, SLOW).toBe('completed')

    await page.goto(`/workflows/${workflowId}/runs/${runId}`)
    await expect(statusChip(page, '완료')).toBeVisible(SLOW)
    await expect(step(page, '긴급한가')).toContainText('아니오')
    await expect(step(page, '담당자 승인')).toHaveCount(0)
  })

  test('a rejection ends the run as failed and a cancel stops a waiting run', async ({
    page,
    request,
  }) => {
    const csrf = await login(request)
    const start = async () => {
      const res = await request.post(`${API}/api/workflows/${workflowId}/runs`, {
        headers: csrf,
        data: { inputs: { inquiry: URGENT } },
      })
      expect(res.ok()).toBeTruthy()
      return ((await res.json()) as Run).id
    }

    const rejected = await start()
    await page.goto(`/workflows/${workflowId}/runs/${rejected}`)
    await expect(statusChip(page, '승인 대기')).toBeVisible(SLOW)
    const approval = await pendingApproval(request, rejected)
    await page.getByRole('button', { name: '거절하고 종료' }).click()
    await expect(statusChip(page, '실패')).toBeVisible(SLOW)
    await expect(runStatus(page)).toContainText('담당자가 긴급 문의 처리를 거절했습니다')
    expect(await auditOf(request, 'approval.decided', approval.id)).toMatchObject({
      outcome: 'denied',
      metadata: expect.objectContaining({ status: 'rejected' }),
    })
    expect(await auditOf(request, 'workflow.run_finish', rejected)).toMatchObject({
      outcome: 'failure',
      metadata: expect.objectContaining({ status: 'failed' }),
    })

    const canceled = await start()
    await page.goto(`/workflows/${workflowId}/runs/${canceled}`)
    await expect(statusChip(page, '승인 대기')).toBeVisible(SLOW)
    await page.getByRole('button', { name: '실행 취소' }).click()
    await expect(statusChip(page, '취소됨')).toBeVisible(SLOW)
    expect(await auditOf(request, 'workflow.run_cancel', canceled)).toMatchObject({
      outcome: 'success',
    })
    // The canceled run's approval left the inbox.
    await expect
      .poll(async () => {
        const inbox = await getJson<{ items: Approval[] }>(
          request,
          `${API}/api/approvals?status=pending`,
        )
        return inbox.items.some((item) => item.workflow_run_id === canceled)
      }, SLOW)
      .toBe(false)
  })
})
