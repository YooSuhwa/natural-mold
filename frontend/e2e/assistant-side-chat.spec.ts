import type { APIRequestContext } from '@playwright/test'

import {
  API_BASE,
  apiDeleteOk,
  apiGetJson,
  apiPostJson,
  expect,
  isRecord,
  loginApi,
  test,
  type CsrfHeaders,
} from './fixtures'

interface SideChatFixture {
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

async function createSideChatFixture(request: APIRequestContext): Promise<SideChatFixture> {
  const csrfHeaders = await loginApi(request)
  const models = await apiGetJson(request, `${API_BASE}/api/models`)
  if (!Array.isArray(models)) throw new Error('Models response was not an array')
  const scripted = models.find(
    (model) => isRecord(model) && model.provider === 'e2e_scripted' && typeof model.id === 'string',
  )
  if (!scripted) throw new Error('The isolated scripted model was not seeded')

  const agent = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
    name: `E2E Side Chat ${Date.now()}`,
    system_prompt: 'Exercise the independent assistant side chat.',
    model_id: scripted.id,
  })
  const agentId = idFrom(agent, 'Side chat agent')
  const conversation = await apiPostJson(
    request,
    `${API_BASE}/api/agents/${agentId}/conversations`,
    csrfHeaders,
    { title: 'E2E Side Chat' },
  )
  return {
    agentId,
    conversationId: idFrom(conversation, 'Side chat conversation'),
    csrfHeaders,
  }
}

test.describe('Independent assistant side chat', () => {
  let fixture: SideChatFixture

  test.beforeAll(async ({ request }) => {
    fixture = await createSideChatFixture(request)
  })

  test.afterAll(async ({ request }) => {
    if (fixture?.agentId) {
      const csrfHeaders = await loginApi(request)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${fixture.agentId}`, csrfHeaders)
    }
  })

  test('isolates the main draft and retains the side transcript across close and navigation', async ({
    page,
    errors,
  }, testInfo) => {
    await page.goto(`/agents/${fixture.agentId}/conversations/${fixture.conversationId}`)
    const main = page.getByRole('main')
    const mainComposer = main.locator('textarea[data-moldy-composer-input="true"]')
    await mainComposer.fill('main draft remains independent')

    await page.getByRole('button', { name: 'AI Assistant' }).click()
    const sideChat = page.getByTestId('assistant-side-chat')
    const sideComposer = sideChat.locator('textarea[data-moldy-composer-input="true"]')
    await sideComposer.fill('side transcript survives')
    const sideRequestPromise = page.waitForRequest(
      (request) =>
        request.method() === 'POST' &&
        request.url().endsWith(`/api/agents/${fixture.agentId}/assistant/message`),
    )
    await sideChat.getByRole('button', { name: '전송' }).click()
    const sideRequest = await sideRequestPromise
    const sidePayload: unknown = sideRequest.postDataJSON()
    expect(isRecord(sidePayload) && typeof sidePayload.session_id === 'string').toBe(true)
    if (!isRecord(sidePayload) || typeof sidePayload.session_id !== 'string') {
      throw new Error('Side assistant request did not include its independent session id')
    }
    expect(sidePayload.session_id).not.toBe(fixture.conversationId)
    expect((await sideRequest.response())?.status()).toBe(200)
    await expect(sideChat.getByText('side transcript survives')).toBeVisible()
    await expect(mainComposer).toHaveValue('main draft remains independent')

    await sideChat.getByRole('button', { name: '닫기' }).click()
    await expect(sideChat).toBeHidden()
    await expect(page.getByRole('button', { name: 'AI Assistant' })).toBeFocused()
    await main.getByRole('button', { name: '더보기 메뉴' }).click()
    const settingsItem = page.getByRole('menuitem', { name: '설정' })
    await settingsItem.hover()
    await settingsItem.dispatchEvent('click')
    await expect(page).toHaveURL(new RegExp(`/agents/${fixture.agentId}/settings`))
    const settingsName = main.getByPlaceholder('에이전트 이름')
    await settingsName.fill('E2E Side Chat unsaved')

    await page.getByRole('button', { name: 'AI Assistant' }).click()
    await expect(
      page.getByTestId('assistant-side-chat').getByText('side transcript survives'),
    ).toBeVisible()
    await expect(page.getByTestId('assistant-side-chat')).not.toHaveAttribute('aria-modal')
    await expect(main.getByRole('button', { name: '저장', exact: true }).first()).toBeEnabled()
    await expect(settingsName).toHaveValue('E2E Side Chat unsaved')
    await page.screenshot({ path: testInfo.outputPath('desktop-retained-side-chat.png') })
    await page.setViewportSize({ width: 1600, height: 900 })
    await page.screenshot({ path: testInfo.outputPath('wide-retained-side-chat.png') })
    expect(errors.console).toEqual([])
    expect(errors.page).toEqual([])
    expect(errors.network).toEqual(['api_request_abort'])
  })

  test('recovers the side composer after a failed request without touching the main transcript', async ({
    page,
  }) => {
    let sideRequests = 0
    let failSideRequests = true
    await page.route(`**/api/agents/${fixture.agentId}/assistant/message`, async (route) => {
      sideRequests += 1
      if (failSideRequests) {
        await route.fulfill({
          status: 503,
          contentType: 'application/json',
          body: JSON.stringify({ error: { code: 'side_chat_probe', message: 'Side chat probe' } }),
        })
        return
      }
      await route.continue()
    })
    await page.goto(`/agents/${fixture.agentId}/conversations/${fixture.conversationId}`)
    const main = page.getByRole('main')
    await page.getByRole('button', { name: 'AI Assistant' }).click()
    const sideChat = page.getByTestId('assistant-side-chat')
    const composer = sideChat.locator('textarea[data-moldy-composer-input="true"]')

    await composer.fill('first side request fails')
    await sideChat.getByRole('button', { name: '전송' }).click()
    await expect.poll(() => sideRequests).toBeGreaterThan(0)
    await sideChat.getByRole('button', { name: '닫기' }).click()
    const failedRequestCount = sideRequests
    failSideRequests = false
    await page.getByRole('button', { name: 'AI Assistant' }).click()
    const recoveredSideChat = page.getByTestId('assistant-side-chat')
    const recoveredComposer = recoveredSideChat.locator(
      'textarea[data-moldy-composer-input="true"]',
    )
    await recoveredComposer.fill('side recovery request')
    await expect(recoveredSideChat.getByRole('button', { name: '전송' })).toBeEnabled()
    await recoveredSideChat.getByRole('button', { name: '전송' }).click()

    await expect.poll(() => sideRequests).toBeGreaterThan(failedRequestCount)
    await expect(recoveredSideChat.getByText('side recovery request')).toBeVisible()
    await expect(main.getByText('side recovery request')).toHaveCount(0)
  })

  test('uses the mobile dialog focus cycle', async ({ page, errors }, testInfo) => {
    await page.goto(`/agents/${fixture.agentId}/settings`)
    const desktopTrigger = page.getByRole('button', { name: 'AI Assistant' })
    await desktopTrigger.click()
    const desktopSideChat = page.getByTestId('assistant-side-chat')
    await expect(desktopSideChat).toBeVisible()
    await desktopSideChat.getByRole('button', { name: '닫기' }).click()
    await page.setViewportSize({ width: 390, height: 844 })
    const trigger = page.getByRole('button', { name: 'AI Assistant' })

    await trigger.click()
    const dialog = page.getByRole('dialog', { name: 'AI Assistant' })
    const composer = dialog.locator('textarea[data-moldy-composer-input="true"]')
    await expect(composer).toBeFocused()
    await expect
      .poll(() => dialog.evaluate((element) => getComputedStyle(element).opacity))
      .toBe('1')
    await page.screenshot({ path: testInfo.outputPath('mobile-side-chat-focus.png') })
    await dialog.getByRole('button', { name: '닫기' }).click()

    await expect(dialog).toBeHidden()
    await expect(trigger).toBeFocused()
    expect(errors.console).toEqual([])
    expect(errors.page).toEqual([])
    expect(errors.network).toEqual([])
  })
})
