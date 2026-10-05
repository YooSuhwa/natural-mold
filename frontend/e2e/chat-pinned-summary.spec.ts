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
import { recordFailureUiEvidence } from './helpers/failure-ui-evidence'

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
    let releaseState: () => void = () => undefined
    const stateGate = new Promise<void>((resolve) => {
      releaseState = resolve
    })
    let heldStateReads = 0
    await fs.mkdir(CAPTURE_DIR, { recursive: true })

    try {
      await page.goto(`/agents/${fixture.agentId}/conversations/${fixture.conversationId}`)
      await sendMessage(page, '고정할 답변을 작성해줘')
      await waitForCompletedReply(request, fixture.conversationId)
      // REST fallback remains actionable while checkpoint hydration is pending.
      await page.route(
        `**/api/conversations/${fixture.conversationId}/langgraph/threads/${fixture.conversationId}/state`,
        async (route) => {
          heldStateReads += 1
          await stateGate
          await route.continue()
        },
      )
      await page.reload()
      await expect.poll(() => heldStateReads).toBeGreaterThan(0)
      const reply = page.getByText('E2E scripted document model is ready.').last()
      await expect(reply).toBeVisible({ timeout: 60_000 })
      const assistantMessage = page.locator('[data-moldy-message-role="assistant"]').last()
      const envelope = await apiGetJson(
        request,
        `${API_BASE}/api/conversations/${fixture.conversationId}/messages`,
      )
      if (!isRecord(envelope) || !Array.isArray(envelope.messages)) {
        throw new Error('REST messages envelope was invalid')
      }
      const source = envelope.messages.find(
        (message: unknown) => isRecord(message) && message.role === 'assistant',
      )
      if (!isRecord(source) || typeof source.runtime_message_id !== 'string') {
        throw new Error('REST assistant did not retain its checkpoint identity')
      }
      await expect(assistantMessage).toHaveAttribute(
        'data-moldy-message-id',
        source.runtime_message_id,
      )
      await assistantMessage.hover()
      const pinResponse = page.waitForResponse(
        (response) =>
          response.request().method() === 'PUT' &&
          response.url() ===
            `${API_BASE}/api/conversations/${fixture.conversationId}/pinned-summary`,
      )
      await assistantMessage.getByRole('button', { name: '대화 요약으로 고정' }).click()
      await apiJson(await pinResponse, 'Pin REST fallback assistant summary')
      // The same identity must also toggle to DELETE before checkpoint hydration.
      const unpinResponse = page.waitForResponse(
        (response) =>
          response.request().method() === 'DELETE' &&
          response.url() ===
            `${API_BASE}/api/conversations/${fixture.conversationId}/pinned-summary`,
      )
      const messageUnpin = assistantMessage.getByRole('button', { name: '대화 요약 고정 해제' })
      await expect(messageUnpin).toBeEnabled()
      await messageUnpin.click()
      expect((await unpinResponse).status()).toBe(204)
      await expect(page.getByText('고정된 대화 요약')).toHaveCount(0)
      const repinResponse = page.waitForResponse(
        (response) =>
          response.request().method() === 'PUT' &&
          response.url() ===
            `${API_BASE}/api/conversations/${fixture.conversationId}/pinned-summary`,
      )
      await assistantMessage.getByRole('button', { name: '대화 요약으로 고정' }).click()
      await apiJson(await repinResponse, 'Repin REST fallback assistant summary')
      releaseState()

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
    } catch (error: unknown) {
      await recordFailureUiEvidence(page, test.info())
      throw error
    } finally {
      releaseState()
      await page.unrouteAll({ behavior: 'wait' })
      await page.close()
      await foreign.dispose()
      await apiDeleteOk(request, `${API_BASE}/api/agents/${fixture.agentId}`, fixture.csrfHeaders)
    }
  })
})
