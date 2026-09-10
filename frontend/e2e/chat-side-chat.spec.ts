import { mkdir } from 'node:fs/promises'
import path from 'node:path'
import type { Locator, Page } from '@playwright/test'
import { API_BASE, apiDeleteOk, apiGetJson, expect, isRecord, test } from './fixtures'
import { sendMessage, setupLangGraphV3Agent } from './langgraph-v3-helpers'

const captures = path.resolve('../output/e2e-captures/20260910-side-chat')

async function capture(page: Page, name: string) {
  if (test.info().project.name !== 'scripted-capture') return
  await page.screenshot({
    path: path.join(captures, name),
    animations: 'disabled',
    timeout: 15_000,
  })
}

async function selectBody(page: Page, body: Locator) {
  await body.scrollIntoViewIfNeeded()
  // Use the browser Selection API to select rendered DOM text, then exercise the real toolbar.
  await body.evaluate((element) => {
    const range = document.createRange()
    range.selectNodeContents(element)
    const selection = window.getSelection()
    selection?.removeAllRanges()
    selection?.addRange(range)
    element.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
  })
  await expect(page.getByTestId('chat-selection-actions')).toBeVisible()
}

test('quotes, comments, independent side run and return to main', async ({ page, request }) => {
  test.setTimeout(240_000)
  page.setDefaultTimeout(15_000)
  await page.setViewportSize({ width: 1600, height: 1000 })
  await mkdir(captures, { recursive: true })
  const setup = await setupLangGraphV3Agent(request)
  let succeeded = false
  try {
    await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
    await test.step('send initial message', () =>
      sendMessage(page, '사이드 채팅을 살펴보겠습니다.'))
    const main = page.locator(`[data-chat-selection-thread="${setup.conversationId}"]`)
    const answer = main
      .locator('[data-moldy-message-role="assistant"] [data-chat-quote-text]')
      .last()
    await expect(answer).toContainText('E2E scripted document model is ready.', { timeout: 60_000 })
    await expect(answer).not.toHaveAttribute('data-chat-streaming', 'true', { timeout: 20_000 })
    await test.step('select rendered answer', () => selectBody(page, answer))
    await test.step('capture selection toolbar', () => capture(page, '01-selection-menu.png'))
    await page
      .getByTestId('chat-selection-actions')
      .getByRole('button', { name: '채팅에 추가', exact: true })
      .click()
    const note = main.getByRole('button', { name: '주석 1', exact: true })
    await expect(note).toBeVisible()
    await note.click()
    const comment = page.getByRole('textbox', { name: '내 댓글' })
    await comment.fill('이 문장의 의미를 설명해 주세요.')
    await page.getByRole('button', { name: '적용', exact: true }).click()
    await note.hover()
    await expect(page.getByRole('tooltip')).toContainText('이 문장의 의미를 설명해 주세요.')
    await expect(page.getByRole('tooltip')).toContainText('에이전트 답변')
    await capture(page, '02-quote-comment-preview.png')
    await main
      .locator('textarea[data-moldy-composer-input]')
      .fill('본 채팅의 초안은 보존해 주세요.')
    await selectBody(page, answer)
    const created = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' && response.url().endsWith('/side-chats'),
    )
    await page
      .getByTestId('chat-selection-actions')
      .getByRole('button', { name: '사이드 채팅에 질문하기' })
      .click()
    const createdResponse = await created
    expect(createdResponse.status()).toBe(201)
    const sideValue: unknown = await createdResponse.json()
    if (!isRecord(sideValue) || typeof sideValue.id !== 'string')
      throw new Error('Missing side conversation')
    const sideId = sideValue.id
    const side = page.locator(`[data-chat-selection-thread="${sideId}"]`)
    await expect(side.getByTestId('quote-context-chips')).toBeVisible({ timeout: 30_000 })
    const sideComposer = side.locator('textarea[data-moldy-composer-input]')
    await sideComposer.fill('선택한 부분을 자세히 알려 주세요.')
    const runRequest = page.waitForRequest(
      (req) => req.url().includes(sideId) && (req.postData() ?? '').includes('resource_context'),
    )
    await sideComposer.press('Enter')
    expect((await runRequest).postData()).toContain('E2E scripted document model is ready.')
    await expect(side.locator('[data-moldy-message-role="assistant"]')).toContainText(
      'E2E scripted document model is ready.',
      { timeout: 60_000 },
    )
    await expect(side.getByTestId('sent-quote-context')).toBeVisible()
    await expect(main.locator('textarea[data-moldy-composer-input]')).toHaveValue(
      '본 채팅의 초안은 보존해 주세요.',
    )
    await expect(main.locator('[data-moldy-message-role="assistant"]')).toHaveCount(1)
    await expect(side.getByRole('button', { name: '채팅에 추가', exact: true })).toBeEnabled({
      timeout: 20_000,
    })
    await capture(page, '03-side-chat-desktop.png')
    await side.getByRole('button', { name: '채팅에 추가', exact: true }).click()
    await expect(main.getByRole('button', { name: '주석 2', exact: true })).toBeVisible()
    await side.getByRole('button', { name: '사이드 채팅 닫기', exact: true }).click()
    await expect(side).toBeHidden()
    await page.getByRole('button', { name: '사이드 채팅', exact: true }).click()
    await expect(side.getByTestId('sent-quote-context')).toBeVisible()
    const listing = await apiGetJson(
      request,
      `${API_BASE}/api/agents/${setup.parentAgentId}/conversations`,
    )
    expect(
      Array.isArray(listing) && listing.some((item) => isRecord(item) && item.id === sideId),
    ).toBe(false)
    await side.getByRole('button', { name: '대화 목록에 저장', exact: true }).click()
    await expect(
      side.getByRole('button', { name: '대화 목록에 저장했어요', exact: true }),
    ).toBeVisible()
    for (const [name, width, height] of [
      ['tablet', 768, 1024],
      ['mobile', 390, 844],
    ] as const) {
      await page.setViewportSize({ width, height })
      await expect(
        page.getByRole('dialog').getByText('사이드 채팅', { exact: true }).first(),
      ).toBeAttached()
      await expect(side.locator('textarea[data-moldy-composer-input]')).toBeVisible({
        timeout: 20_000,
      })
      await capture(page, `04-side-chat-${name}.png`)
      if (name === 'tablet')
        await side.locator('textarea[data-moldy-composer-input]').fill('사이드 초안 보존')
      else
        await expect(side.locator('textarea[data-moldy-composer-input]')).toHaveValue(
          '사이드 초안 보존',
        )
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
        true,
      )
    }
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toBeHidden()
    await expect(main.locator('textarea[data-moldy-composer-input]')).toHaveValue(
      '본 채팅의 초안은 보존해 주세요.',
    )
    await page.reload()
    const reopened = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' && response.url().endsWith('/side-chats'),
    )
    await page.getByRole('button', { name: '사이드 채팅', exact: true }).click()
    const reopenedResponse = await reopened
    expect(reopenedResponse.status()).toBe(200)
    expect((await reopenedResponse.json()).id).toBe(sideId)
    await expect(side.getByTestId('sent-quote-context')).toBeVisible({ timeout: 20_000 })
    succeeded = true
  } finally {
    // The runner removes the throwaway DB on failure; don't mask the original error with cleanup.
    if (succeeded) {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  }
})
