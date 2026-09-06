import fs from 'node:fs/promises'
import path from 'node:path'
import type { APIRequestContext } from '@playwright/test'
import {
  API_BASE,
  apiDeleteOk,
  apiGetJson,
  apiJson,
  apiPostJson,
  expect,
  isRecord,
  loginApi,
  test,
  type CsrfHeaders,
} from './fixtures'
import { sendMessage } from './langgraph-v3-helpers'

const CAPTURE_DIR = path.resolve(
  process.cwd(),
  '..',
  'output',
  'e2e-captures',
  '20260906-chat-modernization',
  'pinned-summary',
)

type PinnedSummaryFixture = {
  readonly agentId: string
  readonly conversationId: string
  readonly csrfHeaders: CsrfHeaders
}

function idFrom(value: unknown, label: string): string {
  if (!isRecord(value) || typeof value.id !== 'string') {
    throw new Error(`${label} did not return an id`)
  }
  return value.id
}

async function createFixture(request: APIRequestContext): Promise<PinnedSummaryFixture> {
  const csrfHeaders = await loginApi(request)
  const models = await apiGetJson(request, `${API_BASE}/api/models`)
  if (!Array.isArray(models)) throw new Error('Models response was not an array')
  const scripted = models.find(
    (model) => isRecord(model) && model.provider === 'e2e_scripted' && typeof model.id === 'string',
  )
  if (!scripted) throw new Error('The isolated scripted model was not seeded')
  const agent = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
    name: `E2E Pinned Summary ${Date.now()}`,
    system_prompt: 'Return the deterministic scripted response.',
    model_id: scripted.id,
  })
  const agentId = idFrom(agent, 'Pinned summary agent')
  const conversation = await apiPostJson(
    request,
    `${API_BASE}/api/agents/${agentId}/conversations`,
    csrfHeaders,
    { title: 'E2E Pinned Summary' },
  )
  return {
    agentId,
    conversationId: idFrom(conversation, 'Pinned summary conversation'),
    csrfHeaders,
  }
}

async function waitForCompletedReply(
  request: APIRequestContext,
  conversationId: string,
): Promise<void> {
  let runId = ''
  await expect
    .poll(
      async () => {
        const queue = await apiGetJson(
          request,
          `${API_BASE}/api/conversations/${conversationId}/run-inputs`,
        )
        if (!isRecord(queue) || !Array.isArray(queue.items)) return null
        const claimed = queue.items.find(
          (item) => isRecord(item) && item.status === 'claimed' && typeof item.run_id === 'string',
        )
        if (!isRecord(claimed) || typeof claimed.run_id !== 'string') return null
        runId = claimed.run_id
        const run = await apiGetJson(
          request,
          `${API_BASE}/api/conversations/${conversationId}/runs/${runId}`,
        )
        return isRecord(run) && typeof run.status === 'string' ? run.status : null
      },
      { timeout: 60_000, intervals: [250, 500, 1000] },
    )
    .toBe('completed')
}

test.describe('Pinned conversation summary', () => {
  test('pins, reloads, rejects a non-owner, and unpins an assistant message', async ({
    page,
    request,
    playwright,
    errors,
  }) => {
    test.setTimeout(180_000)
    const fixture = await createFixture(request)
    const foreign = await playwright.request.newContext({ baseURL: API_BASE })
    await fs.mkdir(CAPTURE_DIR, { recursive: true })

    try {
      await page.goto(`/agents/${fixture.agentId}/conversations/${fixture.conversationId}`)
      await sendMessage(page, '고정할 답변을 작성해줘')
      await waitForCompletedReply(request, fixture.conversationId)
      await page.reload()
      const reply = page.getByText('E2E scripted document model is ready.').last()
      await expect(reply).toBeVisible({ timeout: 60_000 })
      const assistantMessage = page.locator('[data-moldy-message-role="assistant"]').last()
      await assistantMessage.hover()
      await page.getByRole('button', { name: '대화 요약으로 고정' }).last().click()

      await expect(page.getByText('고정된 대화 요약')).toBeVisible()
      await expect(page.getByText('E2E scripted document model is ready.')).toHaveCount(2)
      const persisted = await apiGetJson(
        request,
        `${API_BASE}/api/conversations/${fixture.conversationId}/pinned-summary`,
      )
      if (!isRecord(persisted) || !isRecord(persisted.summary)) {
        throw new Error('Pinned summary response was invalid')
      }
      const sourceMessageId = persisted.summary.source_message_id
      if (typeof sourceMessageId !== 'string') {
        throw new Error('Pinned summary source identity was invalid')
      }

      await page.reload()
      await expect(page.getByText('고정된 대화 요약')).toBeVisible({ timeout: 30_000 })
      await expect(page.getByText('E2E scripted document model is ready.')).toHaveCount(2)
      await page.screenshot({ path: path.join(CAPTURE_DIR, 'pinned-summary-reload.png') })
      await page.setViewportSize({ width: 390, height: 844 })
      await expect(page.getByText('고정된 대화 요약')).toBeVisible()
      await page.screenshot({ path: path.join(CAPTURE_DIR, 'pinned-summary-mobile.png') })

      const foreignEmail = `pinned-summary-${Date.now()}@moldy.dev`
      const registration = await apiJson(
        await foreign.post('/api/auth/register', {
          data: { email: foreignEmail, password: 'correct horse battery staple 42', name: 'Other' },
        }),
        'foreign registration',
      )
      if (!isRecord(registration) || typeof registration.csrf_token !== 'string') {
        throw new Error('Foreign registration did not return CSRF')
      }
      const denied = await foreign.put(
        `/api/conversations/${fixture.conversationId}/pinned-summary`,
        {
          headers: { 'X-CSRF-Token': registration.csrf_token },
          data: { message_id: sourceMessageId },
        },
      )
      expect(denied.status()).toBe(404)

      await page.getByRole('button', { name: '대화 요약 고정 해제' }).first().click()
      await expect(page.getByText('고정된 대화 요약')).toHaveCount(0)
      const cleared = await apiGetJson(
        request,
        `${API_BASE}/api/conversations/${fixture.conversationId}/pinned-summary`,
      )
      expect(cleared).toEqual({ summary: null })
      expect(errors.console).toEqual([])
      expect(errors.page).toEqual([])
    } finally {
      await foreign.dispose()
      await apiDeleteOk(request, `${API_BASE}/api/agents/${fixture.agentId}`, fixture.csrfHeaders)
    }
  })
})
