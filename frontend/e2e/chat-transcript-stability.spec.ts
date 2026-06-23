import type { Locator, Page } from '@playwright/test'
import { API_BASE, apiDeleteOk, expect, test } from './fixtures'
import { sendMessage, setupLangGraphV3Agent } from './langgraph-v3-helpers'

const FRONTEND =
  process.env.E2E_BASE_URL ?? `http://localhost:${process.env.E2E_FRONTEND_PORT ?? '3000'}`
const ASK_USER_FINAL_TEXT = 'E2E ask_user fruit selection received.'
const RICH_OUTPUT_PROMPT =
  '체크리스트, 표, TypeScript 코드, 수식, 이미지, 링크, 인용문, Mermaid 다이어그램을 모두 포함해서 채팅 출력 예시를 보여줘'
const RICH_OUTPUT_TITLE = 'E2E rich output contract'
const RICH_OUTPUT_IMAGE_ALT = 'E2E rich output image'
const RICH_OUTPUT_REFERENCE_URL = 'https://example.com/e2e-chat-rich-output'
const DRAFT_TO_CONVERSATION_URL = /\/agents\/[^/]+\/conversations\/[0-9a-f-]{36}$/

type TranscriptStabilityWindow = Window & {
  __moldyAskUserActivityMaxRows?: number
  __moldyAskUserActivityObserver?: MutationObserver
  __moldyAskUserActivitySnapshots?: string[]
  __moldyAskUserPromptObserver?: MutationObserver
  __moldyAskUserPromptReadyAt?: number
  __moldyAskUserPromptDisappearances?: string[]
}

async function installAskUserActivityObserver(page: Page): Promise<void> {
  await page.evaluate(() => {
    const observedWindow = window as TranscriptStabilityWindow
    observedWindow.__moldyAskUserActivityObserver?.disconnect()
    observedWindow.__moldyAskUserActivityMaxRows = 0
    observedWindow.__moldyAskUserActivitySnapshots = []

    const check = () => {
      const main = document.querySelector('main')
      if (!(main instanceof HTMLElement)) return

      const rows = Array.from(
        main.querySelectorAll('[data-testid="run-activity-strip"] [data-kind="tool"]'),
      ).filter((element) => (element.textContent ?? '').includes('ask_user 실행 중'))
      const count = rows.length
      const previous = observedWindow.__moldyAskUserActivityMaxRows ?? 0
      if (count > previous) {
        observedWindow.__moldyAskUserActivityMaxRows = count
        observedWindow.__moldyAskUserActivitySnapshots?.push(main.innerText.slice(0, 1000))
      }
    }

    const observer = new MutationObserver(check)
    observer.observe(document.body, { childList: true, subtree: true, characterData: true })
    observedWindow.__moldyAskUserActivityObserver = observer
    check()
  })
}

async function expectAskUserActivityNotDuplicated(page: Page): Promise<void> {
  const result = await page.evaluate(() => {
    const observedWindow = window as TranscriptStabilityWindow
    return {
      maxRows: observedWindow.__moldyAskUserActivityMaxRows ?? 0,
      snapshots: observedWindow.__moldyAskUserActivitySnapshots ?? [],
    }
  })
  expect(
    result.maxRows,
    `ask_user activity rows duplicated:\n${result.snapshots.join('\n---\n')}`,
  ).toBeLessThanOrEqual(1)
}

async function installUserPromptStabilityObserver(page: Page, prompt: string): Promise<void> {
  await page.evaluate((expectedPrompt) => {
    const observedWindow = window as TranscriptStabilityWindow
    observedWindow.__moldyAskUserPromptObserver?.disconnect()
    observedWindow.__moldyAskUserPromptReadyAt = undefined
    observedWindow.__moldyAskUserPromptDisappearances = []

    const check = () => {
      const main = document.querySelector('main')
      if (!(main instanceof HTMLElement)) return

      const promptVisible = Array.from(
        main.querySelectorAll('[data-moldy-message-role="user"]'),
      ).some((element) => (element.textContent ?? '').includes(expectedPrompt))
      if (promptVisible) {
        observedWindow.__moldyAskUserPromptReadyAt ??= performance.now()
        return
      }

      if (typeof observedWindow.__moldyAskUserPromptReadyAt === 'number') {
        observedWindow.__moldyAskUserPromptDisappearances?.push(main.innerText.slice(0, 1000))
      }
    }

    const observer = new MutationObserver(check)
    observer.observe(document.body, { childList: true, subtree: true, characterData: true })
    observedWindow.__moldyAskUserPromptObserver = observer
    check()
  }, prompt)
}

async function expectNoUserPromptDisappearance(page: Page): Promise<void> {
  await page.waitForFunction(
    () => {
      const observedWindow = window as TranscriptStabilityWindow
      return typeof observedWindow.__moldyAskUserPromptReadyAt === 'number'
    },
    undefined,
    { timeout: 30_000, polling: 100 },
  )

  const disappearances = await page.evaluate(() => {
    const observedWindow = window as TranscriptStabilityWindow
    return observedWindow.__moldyAskUserPromptDisappearances ?? []
  })
  expect(
    disappearances,
    `user prompt disappeared while ask_user rendered:\n${disappearances.join('\n---\n')}`,
  ).toEqual([])
}

async function expectImageLoaded(image: Locator): Promise<void> {
  await image.scrollIntoViewIfNeeded()
  await expect(image).toBeVisible()
  await expect
    .poll(
      async () =>
        image.evaluate((element) => {
          if (!(element instanceof HTMLImageElement)) return false
          return element.complete && element.naturalWidth > 0 && element.naturalHeight > 0
        }),
      { timeout: 20_000, intervals: [100, 250, 500] },
    )
    .toBe(true)
}

async function expectRichOutputRendered(page: Page): Promise<void> {
  const assistantMessage = page
    .locator('[data-moldy-message-role="assistant"]')
    .filter({ hasText: RICH_OUTPUT_TITLE })
    .last()

  await expect(assistantMessage).toBeVisible({ timeout: 30_000 })
  await expect(assistantMessage.getByText(RICH_OUTPUT_TITLE)).toBeVisible()
  await expect(assistantMessage.getByText('E2E checklist item')).toBeVisible()
  await expect(
    assistantMessage.locator('code').filter({ hasText: 'e2e_inline_code' }),
  ).toBeVisible()
  await expect(assistantMessage.getByRole('table')).toBeVisible()
  await expect(assistantMessage.getByText('E2E table cell')).toBeVisible()
  await expect(assistantMessage.locator('pre').filter({ hasText: 'e2eRichOutput' })).toBeVisible()
  await expect(assistantMessage.locator('.code-block-header button')).toBeVisible()
  await expect(assistantMessage.locator('.katex').first()).toBeVisible()
  await expectImageLoaded(assistantMessage.getByRole('img', { name: RICH_OUTPUT_IMAGE_ALT }))
  await expect(assistantMessage.getByRole('link', { name: 'E2E reference link' })).toHaveAttribute(
    'href',
    RICH_OUTPUT_REFERENCE_URL,
  )
  await expect(assistantMessage.getByText('E2E blockquote remains rendered.')).toBeVisible()
  await expect(assistantMessage.getByText('E2E Mermaid Rendered')).toBeVisible({
    timeout: 30_000,
  })
}

test.describe('Chat transcript stability QA bundle', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.NEXT_PUBLIC_CHAT_RUNTIME !== 'langgraph_v3',
    'Requires NEXT_PUBLIC_CHAT_RUNTIME=langgraph_v3',
  )

  test('shows one ask_user card and keeps the user prompt visible through draft promotion', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(120_000)
    const setup = await setupLangGraphV3Agent(request)
    const prompt = '사과, 포도, 배 중에 하나 선택하는 ask user 해줘'

    try {
      await page.goto(`${FRONTEND}/agents/${setup.parentAgentId}/conversations/new`)
      await expect(page).toHaveURL(new RegExp(`/agents/${setup.parentAgentId}/conversations/new$`))
      await installUserPromptStabilityObserver(page, prompt)

      await sendMessage(page, prompt)
      await expect(page).toHaveURL(DRAFT_TO_CONVERSATION_URL, { timeout: 30_000 })
      await expect(
        page.locator('[data-moldy-message-role="user"]').filter({ hasText: prompt }),
      ).toBeVisible({ timeout: 30_000 })

      const askUserCards = page.locator('[data-tool-ui-id]').filter({ hasText: '🍎 사과' })
      await expect(askUserCards).toHaveCount(1, { timeout: 30_000 })
      await expect(askUserCards.first().getByText('입력이 필요합니다')).toBeVisible()
      await expectNoUserPromptDisappearance(page)

      const askUserCard = askUserCards.first()
      await askUserCard.getByRole('option', { name: /사과/ }).click()
      await askUserCard.getByRole('button', { name: /선택 확인 \(1\)|Confirm \(1\)/ }).click()

      await expect(page.getByText(ASK_USER_FINAL_TEXT)).toBeVisible({ timeout: 60_000 })
      await expect(
        page.locator('[data-moldy-message-role="user"]').filter({ hasText: prompt }),
      ).toBeVisible()
      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('keeps one third-turn ask_user card after earlier chat turns', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(150_000)
    const setup = await setupLangGraphV3Agent(request)
    const firstPrompt = '안녕?'
    const secondPrompt = '반가워'
    const askUserPrompt = '사과, 배, 포도 중에 하나 선택하는 ask user 해줘'

    try {
      await page.goto(`${FRONTEND}/agents/${setup.parentAgentId}/conversations/new`)

      await sendMessage(page, firstPrompt)
      await expect(page).toHaveURL(DRAFT_TO_CONVERSATION_URL, { timeout: 30_000 })
      await expect(page.getByText('E2E scripted document model is ready.').last()).toBeVisible({
        timeout: 30_000,
      })

      await sendMessage(page, secondPrompt)
      await expect(
        page.locator('[data-moldy-message-role="user"]').filter({ hasText: secondPrompt }),
      ).toBeVisible({ timeout: 30_000 })
      await expect(page.getByText('E2E scripted document model is ready.').last()).toBeVisible({
        timeout: 30_000,
      })

      await installUserPromptStabilityObserver(page, askUserPrompt)
      await installAskUserActivityObserver(page)
      await sendMessage(page, askUserPrompt)

      await expect(
        page.locator('[data-moldy-message-role="user"]').filter({ hasText: askUserPrompt }),
      ).toBeVisible({ timeout: 30_000 })
      const askUserCards = page.locator('[data-tool-ui-id]').filter({ hasText: '🍎 사과' })
      await expect(askUserCards).toHaveCount(1, { timeout: 45_000 })
      await expect(page.getByText('네, 골라봐요!').last()).toBeVisible()
      await expect(askUserCards.first().getByText('입력이 필요합니다')).toBeVisible()
      await expectNoUserPromptDisappearance(page)
      await expectAskUserActivityNotDuplicated(page)
      await expect(page.getByText('ask_user 실행 중')).toHaveCount(0)
      const conversationUrl = page.url()
      await page.reload()
      await expect(page).toHaveURL(conversationUrl)
      const hydratedAskUserCards = page.locator('[data-tool-ui-id]').filter({ hasText: '🍎 사과' })
      await expect(hydratedAskUserCards).toHaveCount(1, { timeout: 45_000 })
      await expect(hydratedAskUserCards.first().getByText('입력이 필요합니다')).toBeVisible()
      await expect(
        page.locator('[data-moldy-message-role="user"]').filter({ hasText: askUserPrompt }),
      ).toBeVisible()
      await expect(page.getByText(/Tool call ask_user|선택 창이 취소/)).toHaveCount(0)

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('renders rich assistant outputs without losing the user prompt', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(120_000)
    const setup = await setupLangGraphV3Agent(request)

    try {
      await page.goto(
        `${FRONTEND}/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`,
      )
      await installUserPromptStabilityObserver(page, RICH_OUTPUT_PROMPT)

      await sendMessage(page, RICH_OUTPUT_PROMPT)
      await expect(
        page.locator('[data-moldy-message-role="user"]').filter({ hasText: RICH_OUTPUT_PROMPT }),
      ).toBeVisible({ timeout: 30_000 })
      await expectRichOutputRendered(page)
      await expectNoUserPromptDisappearance(page)

      const conversationUrl = page.url()
      await page.reload()
      await expect(page).toHaveURL(conversationUrl)
      await expect(
        page.locator('[data-moldy-message-role="user"]').filter({ hasText: RICH_OUTPUT_PROMPT }),
      ).toBeVisible({ timeout: 30_000 })
      await expectRichOutputRendered(page)

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })
})
