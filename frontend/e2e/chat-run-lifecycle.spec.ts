import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const BACKEND_PORT = process.env.E2E_BACKEND_PORT ?? '8001'
const API_BASE = process.env.E2E_API_BASE_URL ?? `http://localhost:${BACKEND_PORT}`
const E2E_EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const E2E_PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

type CsrfHeaders = Record<string, string>

interface ModelRow {
  id: string
  provider: string
  model_name: string
}

async function loginApi(request: APIRequestContext): Promise<CsrfHeaders> {
  const res = await request.post(`${API_BASE}/api/auth/login`, {
    data: { email: E2E_EMAIL, password: E2E_PASSWORD },
  })
  expect(res.ok()).toBeTruthy()
  const body = (await res.json()) as { csrf_token: string }
  return { 'X-CSRF-Token': body.csrf_token }
}

async function firstModelId(request: APIRequestContext): Promise<string> {
  const res = await request.get(`${API_BASE}/api/models`)
  expect(res.ok()).toBeTruthy()
  const models = (await res.json()) as { id: string }[]
  expect(models.length).toBeGreaterThan(0)
  return models[0].id
}

async function scriptedModelId(request: APIRequestContext): Promise<string> {
  const res = await request.get(`${API_BASE}/api/models`)
  expect(res.ok()).toBeTruthy()
  const models = (await res.json()) as ModelRow[]
  const model = models.find(
    (item) => item.provider === 'e2e_scripted' && item.model_name === 'document-artifact-scripted',
  )
  expect(model, 'E2E scripted model should be seeded').toBeTruthy()
  return model?.id ?? ''
}

async function createAgent(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  modelId: string,
  name: string,
): Promise<string> {
  const res = await request.post(`${API_BASE}/api/agents`, {
    headers: csrfHeaders,
    data: {
      name,
      system_prompt: 'You are an E2E chat run lifecycle contract agent.',
      model_id: modelId,
    },
  })
  expect(res.ok()).toBeTruthy()
  const agent = (await res.json()) as { id: string }
  return agent.id
}

async function createConversation(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  agentId: string,
  title: string,
): Promise<string> {
  const res = await request.post(`${API_BASE}/api/agents/${agentId}/conversations`, {
    headers: csrfHeaders,
    data: { title },
  })
  expect(res.ok()).toBeTruthy()
  const conversation = (await res.json()) as { id: string }
  return conversation.id
}

async function waitForActiveRun(
  request: APIRequestContext,
  conversationId: string,
): Promise<{ id: string; status: string }> {
  const deadline = Date.now() + 10_000
  while (Date.now() < deadline) {
    const res = await request.get(`${API_BASE}/api/conversations/${conversationId}/runs/active`)
    expect(res.ok()).toBeTruthy()
    const run = (await res.json()) as { id: string; status: string } | null
    if (run) return run
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`Timed out waiting for active run in conversation ${conversationId}`)
}

async function waitForRunStatus(
  request: APIRequestContext,
  conversationId: string,
  runId: string,
  status: string,
): Promise<{ id: string; status: string }> {
  const deadline = Date.now() + 15_000
  while (Date.now() < deadline) {
    const res = await request.get(`${API_BASE}/api/conversations/${conversationId}/runs/${runId}`)
    expect(res.ok()).toBeTruthy()
    const run = (await res.json()) as { id: string; status: string }
    if (run.status === status) return run
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
  throw new Error(`Timed out waiting for run ${runId} status ${status}`)
}

test.describe('Chat run lifecycle API contract', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.E2E_TEST_HELPERS_ENABLED !== 'true',
    'Requires E2E_TEST_HELPERS_ENABLED=true for run seeding',
  )

  test('P1 exposes active conversation run state through list, active-run, and messages APIs', async ({
    request,
  }) => {
    const csrfHeaders = await loginApi(request)
    const modelId = await firstModelId(request)
    const agentId = await createAgent(
      request,
      csrfHeaders,
      modelId,
      `E2E Chat Run P1 ${Date.now()}`,
    )

    try {
      const conversationId = await createConversation(
        request,
        csrfHeaders,
        agentId,
        'P1 active run contract',
      )

      const seedRes = await request.post(`${API_BASE}/api/e2e/conversations/${conversationId}/runs`, {
        headers: csrfHeaders,
        data: {
          status: 'running',
          source: 'chat',
          input_preview: 'P1 active run contract',
        },
      })
      expect(seedRes.ok()).toBeTruthy()
      const seededRun = (await seedRes.json()) as { id: string; status: string }
      expect(seededRun.status).toBe('running')

      const pageRes = await request.get(`${API_BASE}/api/agents/${agentId}/conversations/page`)
      expect(pageRes.ok()).toBeTruthy()
      const page = (await pageRes.json()) as {
        items: { id: string; active_run: { id: string; status: string } | null }[]
      }
      const item = page.items.find((conversation) => conversation.id === conversationId)
      expect(item?.active_run?.id).toBe(seededRun.id)
      expect(item?.active_run?.status).toBe('running')

      const activeRes = await request.get(
        `${API_BASE}/api/conversations/${conversationId}/runs/active`,
      )
      expect(activeRes.ok()).toBeTruthy()
      const activeRun = (await activeRes.json()) as { id: string; status: string }
      expect(activeRun.id).toBe(seededRun.id)
      expect(activeRun.status).toBe('running')

      const messagesRes = await request.get(`${API_BASE}/api/conversations/${conversationId}/messages`)
      expect(messagesRes.ok()).toBeTruthy()
      const messages = (await messagesRes.json()) as {
        active_run: { id: string; status: string } | null
      }
      expect(messages.active_run?.id).toBe(seededRun.id)
      expect(messages.active_run?.status).toBe('running')

      const forbiddenRes = await request.get(
        `${API_BASE}/api/conversations/00000000-0000-0000-0000-000000000099/runs/active`,
      )
      expect(forbiddenRes.status()).toBe(404)
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrfHeaders })
    }
  })

  test('P2 keeps a run active across session navigation and re-attaches on return', async ({
    page,
    request,
    errors,
  }) => {
    const csrfHeaders = await loginApi(request)
    const modelId = await scriptedModelId(request)
    const agentId = await createAgent(
      request,
      csrfHeaders,
      modelId,
      `E2E Chat Run P2 ${Date.now()}`,
    )
    const captureDir = path.join(
      '..',
      'output',
      'e2e-captures',
      '20260610-chat-run-lifecycle',
    )

    try {
      const conversationA = await createConversation(
        request,
        csrfHeaders,
        agentId,
        'P2 slow run A',
      )
      const conversationB = await createConversation(
        request,
        csrfHeaders,
        agentId,
        'P2 other session B',
      )
      await fs.mkdir(captureDir, { recursive: true })

      await page.goto(`/agents/${agentId}/conversations/${conversationA}`)
      const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
      await expect(composer).toBeVisible()
      await composer.fill('E2E_SLOW_STREAM')
      await composer.press('Enter')

      const spinnerA = page.locator(`[data-moldy-run-spinner="${conversationA}"]`)
      await expect(spinnerA).toBeVisible({ timeout: 10_000 })
      await page.screenshot({
        path: path.join(captureDir, 'p2-spinner-conversation-a.png'),
        fullPage: true,
      })

      await page.goto(`/agents/${agentId}/conversations/${conversationB}`)
      await expect(page).toHaveURL(new RegExp(`/conversations/${conversationB}$`))
      await expect(spinnerA).toBeVisible({ timeout: 10_000 })
      await page.screenshot({
        path: path.join(captureDir, 'p2-spinner-while-other-session-open.png'),
        fullPage: true,
      })

      await page.goto(`/agents/${agentId}/conversations/${conversationA}`)
      await expect(page.getByText(/E2E slow stream completed/)).toBeVisible({
        timeout: 30_000,
      })
      await expect(spinnerA).toBeHidden({ timeout: 10_000 })
      await page.screenshot({
        path: path.join(captureDir, 'p2-reattached-completed.png'),
        fullPage: true,
      })

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrfHeaders })
    }
  })

  test('P4 refresh restores the running indicator and re-attaches to the active run', async ({
    page,
    request,
    errors,
  }) => {
    const csrfHeaders = await loginApi(request)
    const modelId = await scriptedModelId(request)
    const agentId = await createAgent(
      request,
      csrfHeaders,
      modelId,
      `E2E Chat Run P4 ${Date.now()}`,
    )
    const captureDir = path.join(
      '..',
      'output',
      'e2e-captures',
      '20260610-chat-run-lifecycle',
    )

    try {
      const conversationId = await createConversation(
        request,
        csrfHeaders,
        agentId,
        'P4 refresh restore',
      )
      await fs.mkdir(captureDir, { recursive: true })

      await page.goto(`/agents/${agentId}/conversations/${conversationId}`)
      const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
      await expect(composer).toBeVisible()
      await composer.fill('E2E_SLOW_STREAM')
      await composer.press('Enter')

      const spinner = page.locator(`[data-moldy-run-spinner="${conversationId}"]`)
      await expect(spinner).toBeVisible({ timeout: 10_000 })

      // F5 — durable run lifecycle 의 대표 시나리오: 새로고침 후에도 스피너가
      // 복원되고, envelope 의 active_run 으로 스트림에 재attach 해 답변을 끝까지 받는다.
      await page.reload()

      await expect(spinner).toBeVisible({ timeout: 10_000 })
      await page.screenshot({
        path: path.join(captureDir, 'p4-spinner-after-refresh.png'),
        fullPage: true,
      })

      await expect(page.getByText(/E2E slow stream completed/)).toBeVisible({
        timeout: 30_000,
      })
      await expect(spinner).toBeHidden({ timeout: 10_000 })
      await page.screenshot({
        path: path.join(captureDir, 'p4-reattached-completed-after-refresh.png'),
        fullPage: true,
      })

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrfHeaders })
    }
  })

  test('P3 Stop cancels the server run and clears the session spinner', async ({
    page,
    request,
    errors,
  }) => {
    const csrfHeaders = await loginApi(request)
    const modelId = await scriptedModelId(request)
    const agentId = await createAgent(
      request,
      csrfHeaders,
      modelId,
      `E2E Chat Run P3 Cancel ${Date.now()}`,
    )
    const captureDir = path.join(
      '..',
      'output',
      'e2e-captures',
      '20260610-chat-run-lifecycle',
    )

    try {
      const conversationId = await createConversation(
        request,
        csrfHeaders,
        agentId,
        'P3 cancel slow run',
      )
      await fs.mkdir(captureDir, { recursive: true })

      await page.goto(`/agents/${agentId}/conversations/${conversationId}`)
      const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
      await expect(composer).toBeVisible()
      await composer.fill('E2E_SLOW_STREAM')
      await composer.press('Enter')

      const spinner = page.locator(`[data-moldy-run-spinner="${conversationId}"]`)
      await expect(spinner).toBeVisible({ timeout: 10_000 })
      const activeRun = await waitForActiveRun(request, conversationId)

      const cancelResponsePromise = page.waitForResponse(
        (response) =>
          response.request().method() === 'POST' &&
          response.url().includes(`/api/conversations/${conversationId}/runs/${activeRun.id}/cancel`),
      )
      await page.locator('[data-moldy-stop-button="true"]').click()
      const cancelResponse = await cancelResponsePromise
      expect(cancelResponse.ok()).toBeTruthy()

      await waitForRunStatus(request, conversationId, activeRun.id, 'canceled')
      await expect(spinner).toBeHidden({ timeout: 10_000 })
      await expect(page.getByText(/중단됨|Canceled/)).toBeVisible({ timeout: 10_000 })
      await page.screenshot({
        path: path.join(captureDir, 'p3-canceled-run.png'),
        fullPage: true,
      })

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrfHeaders })
    }
  })

  test('P3 stale active run clears spinner and shows stale copy', async ({
    page,
    request,
    errors,
  }) => {
    const csrfHeaders = await loginApi(request)
    const modelId = await scriptedModelId(request)
    const agentId = await createAgent(
      request,
      csrfHeaders,
      modelId,
      `E2E Chat Run P3 Stale ${Date.now()}`,
    )
    const captureDir = path.join(
      '..',
      'output',
      'e2e-captures',
      '20260610-chat-run-lifecycle',
    )

    try {
      const conversationId = await createConversation(
        request,
        csrfHeaders,
        agentId,
        'P3 stale run',
      )
      await fs.mkdir(captureDir, { recursive: true })

      const seedRes = await request.post(`${API_BASE}/api/e2e/conversations/${conversationId}/runs`, {
        headers: csrfHeaders,
        data: {
          status: 'running',
          source: 'chat',
          input_preview: 'stale run',
        },
      })
      expect(seedRes.ok()).toBeTruthy()
      const seededRun = (await seedRes.json()) as { id: string; status: string }
      expect(seededRun.status).toBe('running')

      const heartbeatRes = await request.patch(
        `${API_BASE}/api/e2e/conversations/${conversationId}/runs/${seededRun.id}/heartbeat`,
        {
          headers: csrfHeaders,
          data: { heartbeat_age_seconds: 900 },
        },
      )
      expect(heartbeatRes.ok()).toBeTruthy()

      await page.goto(`/agents/${agentId}/conversations/${conversationId}`)
      await expect(page.locator('p').filter({ hasText: /응답이 끊어져 일부가 누락/ })).toBeVisible({
        timeout: 15_000,
      })
      await waitForRunStatus(request, conversationId, seededRun.id, 'stale')
      await expect(page.locator(`[data-moldy-run-spinner="${conversationId}"]`)).toBeHidden({
        timeout: 10_000,
      })
      await page.screenshot({
        path: path.join(captureDir, 'p3-stale-run.png'),
        fullPage: true,
      })

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrfHeaders })
    }
  })

  test('P3 interrupted run shows action-required marker without spinner', async ({
    page,
    request,
    errors,
  }) => {
    const csrfHeaders = await loginApi(request)
    const modelId = await scriptedModelId(request)
    const agentId = await createAgent(
      request,
      csrfHeaders,
      modelId,
      `E2E Chat Run P3 HITL ${Date.now()}`,
    )
    const captureDir = path.join(
      '..',
      'output',
      'e2e-captures',
      '20260610-chat-run-lifecycle',
    )

    try {
      const conversationId = await createConversation(
        request,
        csrfHeaders,
        agentId,
        'P3 action required run',
      )
      await fs.mkdir(captureDir, { recursive: true })

      const seedRes = await request.post(`${API_BASE}/api/e2e/conversations/${conversationId}/runs`, {
        headers: csrfHeaders,
        data: {
          status: 'interrupted',
          source: 'chat',
          input_preview: 'needs approval',
          interrupt_id: 'e2e-approval',
        },
      })
      expect(seedRes.ok()).toBeTruthy()
      const seededRun = (await seedRes.json()) as { id: string; status: string }
      expect(seededRun.status).toBe('interrupted')

      await page.goto(`/agents/${agentId}/conversations/${conversationId}`)
      await expect(page.locator(`[data-moldy-run-attention="${conversationId}"]`)).toBeVisible({
        timeout: 10_000,
      })
      await expect(page.locator(`[data-moldy-run-spinner="${conversationId}"]`)).toBeHidden()
      await page.screenshot({
        path: path.join(captureDir, 'p3-interrupted-action-required.png'),
        fullPage: true,
      })

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await request.delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrfHeaders })
    }
  })
})
