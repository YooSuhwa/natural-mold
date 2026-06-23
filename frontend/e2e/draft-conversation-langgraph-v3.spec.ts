import { test, expect, API_BASE, apiDeleteOk, apiGetJson, isRecord } from './fixtures'
import type { APIRequestContext, Page } from '@playwright/test'
import { sendMessage, setupLangGraphV3Agent } from './langgraph-v3-helpers'

interface ConversationRow {
  readonly id: string
}

const EMPTY_STATE_TEXT = '대화를 시작해보세요.'
const FIRST_TURN_RESPONSE_TEXT = 'E2E scripted document model is ready.'

type EmptyStateObserverWindow = Window & {
  __moldyEmptyStateObserver?: MutationObserver
  __moldyEmptyStateReadyAt?: number
  __moldyEmptyStateReappearances?: string[]
  __moldyEmptyStateReady?: boolean
  __moldyMessageDisappearanceObserver?: MutationObserver
  __moldyMessageDisappearanceReadyAt?: number
  __moldyMessageDisappearances?: string[]
  __moldyEditedUserDuplicateObserver?: MutationObserver
  __moldyEditedUserDuplicateReadyAt?: number
  __moldyEditedUserDuplicates?: string[]
  __moldyStaleAssistantAfterEditObserver?: MutationObserver
  __moldyStaleAssistantAfterEditReadyAt?: number
  __moldyStaleAssistantAfterEditFrames?: string[]
  __moldyWrongBranchIndexObserver?: MutationObserver
  __moldyWrongBranchIndexReadyAt?: number
  __moldyWrongBranchIndexFrames?: string[]
  __moldyUserTextStabilityObserver?: MutationObserver
  __moldyUserTextStabilityReadyAt?: number
  __moldyUserTextFlickerFrames?: string[]
  __moldyUserTextExpectedTexts?: string[]
  __moldyStableAssistantCountSince?: number
  __moldyStableAssistantCountLast?: number
  __moldyStableAssistantCountText?: string
}

function conversationRows(value: unknown): ConversationRow[] {
  if (!Array.isArray(value)) {
    throw new Error('conversation list did not return conversation rows')
  }
  return value.map((row) => {
    if (!isRecord(row) || typeof row.id !== 'string') {
      throw new Error('conversation list row did not include an id')
    }
    return { id: row.id }
  })
}

async function listConversationIds(request: APIRequestContext, agentId: string): Promise<string[]> {
  const rows = conversationRows(
    await apiGetJson(request, `${API_BASE}/api/agents/${agentId}/conversations`),
  )
  return rows.map((conversation) => conversation.id)
}

function draftConversationId(value: unknown): string {
  if (!isRecord(value) || typeof value.id !== 'string') {
    throw new Error('draft conversation response did not include an id')
  }
  return value.id
}

function watchDraftConversationIds(page: Page, agentId: string): string[] {
  const ids: string[] = []
  page.on('response', (response) => {
    if (response.request().method() !== 'POST') return
    if (!response.url().endsWith(`/api/agents/${agentId}/conversations/draft`)) return
    if (!response.ok()) return
    void response
      .json()
      .then((body: unknown) => {
        ids.push(draftConversationId(body))
      })
      .catch(() => undefined)
  })
  return ids
}

async function expectConversationDetailStatus(
  request: APIRequestContext,
  conversationId: string,
  status: number,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const response = await request.get(`${API_BASE}/api/conversations/${conversationId}`)
        return response.status()
      },
      { timeout: 10_000, intervals: [250, 500, 1000] },
    )
    .toBe(status)
}

async function expectConversationMessagesStatus(
  request: APIRequestContext,
  conversationId: string,
  status: number,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const response = await request.get(
          `${API_BASE}/api/conversations/${conversationId}/messages`,
        )
        return response.status()
      },
      { timeout: 10_000, intervals: [250, 500, 1000] },
    )
    .toBe(status)
}

async function waitRunIdle(request: APIRequestContext, conversationId: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const run = await apiGetJson(
          request,
          `${API_BASE}/api/conversations/${conversationId}/runs/active`,
        )
        return run === null ? null : 'active'
      },
      { timeout: 60_000, intervals: [500, 1000, 2000] },
    )
    .toBe(null)
}

async function waitRunActive(request: APIRequestContext, conversationId: string): Promise<void> {
  await expect
    .poll(
      async () => {
        const run = await apiGetJson(
          request,
          `${API_BASE}/api/conversations/${conversationId}/runs/active`,
        )
        return run === null ? null : 'active'
      },
      { timeout: 10_000, intervals: [100, 250, 500] },
    )
    .toBe('active')
}

async function visibleStopButtonCount(page: Page): Promise<number> {
  return page
    .getByRole('button', { name: '중단' })
    .evaluateAll(
      (buttons) =>
        buttons.filter(
          (button) =>
            button instanceof HTMLElement &&
            window.getComputedStyle(button).visibility !== 'hidden' &&
            window.getComputedStyle(button).display !== 'none' &&
            button.getClientRects().length > 0,
        ).length,
    )
}

async function waitForVisibleStopButton(page: Page): Promise<void> {
  await expect
    .poll(() => visibleStopButtonCount(page), {
      timeout: 20_000,
      intervals: [100, 250, 500],
    })
    .toBeGreaterThan(0)
}

async function waitForNoVisibleStopButton(page: Page): Promise<void> {
  await expect
    .poll(() => visibleStopButtonCount(page), { timeout: 60_000, intervals: [250, 500, 1000] })
    .toBe(0)
}

async function expectAssistantMessageCountStable(
  page: Page,
  assistantText: string,
  expectedCount: number,
  stableDurationMs = 1500,
): Promise<void> {
  await page.waitForFunction(
    ({ text, count, durationMs }) => {
      const observedWindow = window as EmptyStateObserverWindow
      const actualCount = Array.from(
        document.querySelectorAll('[data-moldy-message-role="assistant"]'),
      ).filter((element) => (element.textContent ?? '').includes(text)).length

      if (
        observedWindow.__moldyStableAssistantCountText !== text ||
        observedWindow.__moldyStableAssistantCountLast !== actualCount
      ) {
        observedWindow.__moldyStableAssistantCountText = text
        observedWindow.__moldyStableAssistantCountLast = actualCount
        observedWindow.__moldyStableAssistantCountSince = performance.now()
        return false
      }

      const since = observedWindow.__moldyStableAssistantCountSince
      return (
        actualCount === count &&
        typeof since === 'number' &&
        performance.now() - since >= durationMs
      )
    },
    { text: assistantText, count: expectedCount, durationMs: stableDurationMs },
    { timeout: stableDurationMs + 5000, polling: 100 },
  )
}

async function installEmptyStateReappearanceObserver(page: Page, prompt: string): Promise<void> {
  await page.evaluate(
    ({ emptyStateText, userPrompt }) => {
      const observedWindow = window as EmptyStateObserverWindow
      observedWindow.__moldyEmptyStateObserver?.disconnect()
      observedWindow.__moldyEmptyStateReappearances = []
      observedWindow.__moldyEmptyStateReady = false
      observedWindow.__moldyEmptyStateReadyAt = undefined

      const elementHasSentPrompt = (element: Element): boolean => {
        if (
          element instanceof HTMLInputElement ||
          element instanceof HTMLTextAreaElement ||
          element instanceof HTMLScriptElement ||
          element instanceof HTMLStyleElement
        ) {
          return false
        }
        return (element.textContent ?? '').includes(userPrompt)
      }

      const check = () => {
        const surface = document.querySelector('main') ?? document.body
        if (!surface) return
        const sentPromptVisible = Array.from(surface.querySelectorAll('*')).some(
          elementHasSentPrompt,
        )
        const mainText = surface.innerText
        const emptyStateVisible = (mainText ?? '').includes(emptyStateText)
        if (sentPromptVisible && !emptyStateVisible) {
          observedWindow.__moldyEmptyStateReady = true
          observedWindow.__moldyEmptyStateReadyAt ??= performance.now()
        }
        if (observedWindow.__moldyEmptyStateReady && emptyStateVisible) {
          observedWindow.__moldyEmptyStateReappearances?.push((mainText ?? '').slice(0, 1000))
        }
      }

      const observer = new MutationObserver(check)
      observer.observe(document.body, { childList: true, subtree: true, characterData: true })
      observedWindow.__moldyEmptyStateObserver = observer
      check()
    },
    { emptyStateText: EMPTY_STATE_TEXT, userPrompt: prompt },
  )
}

async function emptyStateReappearances(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const observedWindow = window as EmptyStateObserverWindow
    return observedWindow.__moldyEmptyStateReappearances ?? []
  })
}

async function expectNoEmptyStateReappearance(page: Page, durationMs = 1500): Promise<void> {
  await page.waitForFunction(
    (stableDurationMs) => {
      const observedWindow = window as EmptyStateObserverWindow
      const readyAt = observedWindow.__moldyEmptyStateReadyAt
      return typeof readyAt === 'number' && performance.now() - readyAt >= stableDurationMs
    },
    durationMs,
    { timeout: durationMs + 5000, polling: 100 },
  )
  const reappearances = await emptyStateReappearances(page)
  expect(
    reappearances,
    `empty state reappeared after first user message:\n${reappearances.join('\n---\n')}`,
  ).toEqual([])
}

async function installMessageDisappearanceObserver(
  page: Page,
  prompt: string,
  response: string,
): Promise<void> {
  await page.evaluate(
    ({ assistantResponse, userPrompt }) => {
      const observedWindow = window as EmptyStateObserverWindow
      observedWindow.__moldyMessageDisappearanceObserver?.disconnect()
      observedWindow.__moldyMessageDisappearances = []
      observedWindow.__moldyMessageDisappearanceReadyAt = undefined

      const check = () => {
        const main = document.querySelector('main')
        if (!(main instanceof HTMLElement)) return
        const mainText = main.innerText
        const promptVisible = mainText.includes(userPrompt)
        const responseVisible = mainText.includes(assistantResponse)
        const readyAt = observedWindow.__moldyMessageDisappearanceReadyAt
        const messages = Array.from(
          main.querySelectorAll<HTMLElement>('[data-moldy-message-role]'),
        ).map((element) => {
          const role = element.dataset.moldyMessageRole ?? 'unknown'
          const id = element.dataset.moldyMessageId ?? 'no-id'
          return `${role}:${id}:${element.innerText.slice(0, 200)}`
        })
        if (promptVisible && responseVisible) {
          observedWindow.__moldyMessageDisappearanceReadyAt ??= performance.now()
          return
        }
        if (typeof readyAt === 'number' && (!promptVisible || !responseVisible)) {
          observedWindow.__moldyMessageDisappearances?.push(
            `${window.location.pathname}\nmessages=${messages.join('\n')}\n${mainText.slice(
              0,
              1000,
            )}`,
          )
        }
      }

      const observer = new MutationObserver(check)
      observer.observe(document.body, { childList: true, subtree: true, characterData: true })
      observedWindow.__moldyMessageDisappearanceObserver = observer
      check()
    },
    { assistantResponse: response, userPrompt: prompt },
  )
}

async function messageDisappearances(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const observedWindow = window as EmptyStateObserverWindow
    return observedWindow.__moldyMessageDisappearances ?? []
  })
}

async function expectNoMessageDisappearance(page: Page, durationMs = 1500): Promise<void> {
  await page.waitForFunction(
    (stableDurationMs) => {
      const observedWindow = window as EmptyStateObserverWindow
      const readyAt = observedWindow.__moldyMessageDisappearanceReadyAt
      return typeof readyAt === 'number' && performance.now() - readyAt >= stableDurationMs
    },
    durationMs,
    { timeout: durationMs + 5000, polling: 100 },
  )
  const disappearances = await messageDisappearances(page)
  expect(
    disappearances,
    `message content disappeared after streamed response:\n${disappearances.join('\n---\n')}`,
  ).toEqual([])
}

async function installEditedUserDuplicateObserver(page: Page, editedText: string): Promise<void> {
  await page.evaluate((text) => {
    const observedWindow = window as EmptyStateObserverWindow
    observedWindow.__moldyEditedUserDuplicateObserver?.disconnect()
    observedWindow.__moldyEditedUserDuplicates = []
    observedWindow.__moldyEditedUserDuplicateReadyAt = undefined

    const userMessageSnapshots = (): string[] =>
      Array.from(document.querySelectorAll<HTMLElement>('[data-moldy-message-role="user"]')).map(
        (element) => {
          const id = element.dataset.moldyMessageId ?? 'no-id'
          return `${id}:${element.innerText.slice(0, 200)}`
        },
      )

    const check = () => {
      const snapshots = userMessageSnapshots()
      const editedCount = snapshots.filter((snapshot) => snapshot.includes(text)).length
      if (editedCount > 0) {
        observedWindow.__moldyEditedUserDuplicateReadyAt ??= performance.now()
      }
      if (snapshots.length > 1 || editedCount > 1) {
        observedWindow.__moldyEditedUserDuplicates?.push(
          `${window.location.pathname}\n${snapshots.join('\n')}`,
        )
      }
    }

    const observer = new MutationObserver(check)
    observer.observe(document.body, { childList: true, subtree: true, characterData: true })
    observedWindow.__moldyEditedUserDuplicateObserver = observer
    check()
  }, editedText)
}

async function editedUserDuplicates(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const observedWindow = window as EmptyStateObserverWindow
    return observedWindow.__moldyEditedUserDuplicates ?? []
  })
}

async function expectNoEditedUserDuplicate(page: Page, durationMs = 1500): Promise<void> {
  await page.waitForFunction(
    (stableDurationMs) => {
      const observedWindow = window as EmptyStateObserverWindow
      const readyAt = observedWindow.__moldyEditedUserDuplicateReadyAt
      return typeof readyAt === 'number' && performance.now() - readyAt >= stableDurationMs
    },
    durationMs,
    { timeout: durationMs + 5000, polling: 100 },
  )
  const duplicates = await editedUserDuplicates(page)
  expect(
    duplicates,
    `edited user message rendered more than once:\n${duplicates.join('\n---\n')}`,
  ).toEqual([])
}

async function installStaleAssistantAfterEditObserver(
  page: Page,
  editedText: string,
  staleAssistantText: string,
): Promise<void> {
  await page.evaluate(
    ({ editedPrompt, staleResponse }) => {
      const observedWindow = window as EmptyStateObserverWindow
      observedWindow.__moldyStaleAssistantAfterEditObserver?.disconnect()
      observedWindow.__moldyStaleAssistantAfterEditFrames = []
      observedWindow.__moldyStaleAssistantAfterEditReadyAt = undefined

      const messageSnapshots = (): string[] =>
        Array.from(document.querySelectorAll<HTMLElement>('[data-moldy-message-role]')).map(
          (element) => {
            const role = element.dataset.moldyMessageRole ?? 'unknown'
            const id = element.dataset.moldyMessageId ?? 'no-id'
            return `${role}:${id}:${element.innerText.slice(0, 240)}`
          },
        )

      const check = () => {
        const main = document.querySelector('main')
        if (!(main instanceof HTMLElement)) return
        const mainText = main.innerText
        if (!mainText.includes(editedPrompt)) return
        observedWindow.__moldyStaleAssistantAfterEditReadyAt ??= performance.now()
        if (!mainText.includes(staleResponse)) return
        observedWindow.__moldyStaleAssistantAfterEditFrames?.push(
          `${window.location.pathname}\nmessages=${messageSnapshots().join('\n')}`,
        )
      }

      const observer = new MutationObserver(check)
      observer.observe(document.body, { childList: true, subtree: true, characterData: true })
      observedWindow.__moldyStaleAssistantAfterEditObserver = observer
      check()
    },
    { editedPrompt: editedText, staleResponse: staleAssistantText },
  )
}

async function staleAssistantAfterEditFrames(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const observedWindow = window as EmptyStateObserverWindow
    return observedWindow.__moldyStaleAssistantAfterEditFrames ?? []
  })
}

async function expectNoStaleAssistantAfterEdit(page: Page, durationMs = 1000): Promise<void> {
  await page.waitForFunction(
    (stableDurationMs) => {
      const observedWindow = window as EmptyStateObserverWindow
      const readyAt = observedWindow.__moldyStaleAssistantAfterEditReadyAt
      return typeof readyAt === 'number' && performance.now() - readyAt >= stableDurationMs
    },
    durationMs,
    { timeout: durationMs + 5000, polling: 50 },
  )
  const frames = await staleAssistantAfterEditFrames(page)
  expect(
    frames,
    `stale assistant reply remained visible after edited user message:\n${frames.join('\n---\n')}`,
  ).toEqual([])
}

async function editVisibleUserMessage(
  page: Page,
  currentText: string,
  nextText: string,
): Promise<void> {
  const userMessage = page.locator('[data-moldy-message-role="user"]').filter({
    hasText: currentText,
  })
  await expect(userMessage).toHaveCount(1, { timeout: 20_000 })
  await userMessage.last().hover()
  await userMessage.last().getByRole('button', { name: '편집' }).click()

  const editInput = page.locator('textarea:not([data-moldy-composer-input="true"])').last()
  await expect(editInput).toBeVisible({ timeout: 10_000 })
  await editInput.fill(nextText)
  await page.getByRole('button', { name: '저장' }).click()
}

async function installWrongBranchIndexObserver(
  page: Page,
  messageText: string,
  wrongBranchText: string,
): Promise<void> {
  await page.evaluate(
    ({ text, wrong }) => {
      const observedWindow = window as EmptyStateObserverWindow
      observedWindow.__moldyWrongBranchIndexObserver?.disconnect()
      observedWindow.__moldyWrongBranchIndexFrames = []
      observedWindow.__moldyWrongBranchIndexReadyAt = undefined

      const check = () => {
        const userMessages = Array.from(
          document.querySelectorAll<HTMLElement>('[data-moldy-message-role="user"]'),
        )
        const matchingMessage = userMessages.find((element) => element.innerText.includes(text))
        if (!matchingMessage) return
        observedWindow.__moldyWrongBranchIndexReadyAt ??= performance.now()
        if (!matchingMessage.innerText.includes(wrong)) return
        observedWindow.__moldyWrongBranchIndexFrames?.push(
          `${window.location.pathname}\n${matchingMessage.innerText}`,
        )
      }

      const observer = new MutationObserver(check)
      observer.observe(document.body, { childList: true, subtree: true, characterData: true })
      observedWindow.__moldyWrongBranchIndexObserver = observer
      check()
    },
    { text: messageText, wrong: wrongBranchText },
  )
}

async function expectNoWrongBranchIndex(page: Page, durationMs = 1000): Promise<void> {
  await page.waitForFunction(
    (stableDurationMs) => {
      const observedWindow = window as EmptyStateObserverWindow
      const readyAt = observedWindow.__moldyWrongBranchIndexReadyAt
      return typeof readyAt === 'number' && performance.now() - readyAt >= stableDurationMs
    },
    durationMs,
    { timeout: durationMs + 5000, polling: 50 },
  )
  const frames = await page.evaluate(() => {
    const observedWindow = window as EmptyStateObserverWindow
    return observedWindow.__moldyWrongBranchIndexFrames ?? []
  })
  expect(
    frames,
    `edited user message showed the wrong branch index:\n${frames.join('\n---\n')}`,
  ).toEqual([])
}

async function installUserTextStabilityObserver(
  page: Page,
  texts: readonly string[],
): Promise<void> {
  await page.evaluate((expectedTexts) => {
    const observedWindow = window as EmptyStateObserverWindow
    observedWindow.__moldyUserTextStabilityObserver?.disconnect()
    observedWindow.__moldyUserTextStabilityReadyAt = undefined
    observedWindow.__moldyUserTextFlickerFrames = []
    observedWindow.__moldyUserTextExpectedTexts = [...expectedTexts]

    const userMessageSnapshots = (): readonly string[] =>
      Array.from(document.querySelectorAll<HTMLElement>('[data-moldy-message-role="user"]')).map(
        (element) => {
          const id = element.dataset.moldyMessageId ?? 'no-id'
          return `${id}:${element.innerText.replace(/\s+/g, ' ').trim()}`
        },
      )

    const check = () => {
      const snapshots = userMessageSnapshots()
      const visibleTexts = snapshots.join('\n')
      const allVisible = expectedTexts.every((text) => visibleTexts.includes(text))
      if (allVisible) {
        observedWindow.__moldyUserTextStabilityReadyAt ??= performance.now()
        return
      }
      if (typeof observedWindow.__moldyUserTextStabilityReadyAt !== 'number') return
      observedWindow.__moldyUserTextFlickerFrames?.push(
        `${window.location.pathname}\n${snapshots.join('\n')}`,
      )
    }

    const observer = new MutationObserver(check)
    observer.observe(document.body, { childList: true, subtree: true, characterData: true })
    observedWindow.__moldyUserTextStabilityObserver = observer
    check()
  }, texts)
}

async function expectNoUserTextFlicker(page: Page, durationMs = 1500): Promise<void> {
  await page.waitForFunction(
    (stableDurationMs) => {
      const observedWindow = window as EmptyStateObserverWindow
      const expectedTexts = observedWindow.__moldyUserTextExpectedTexts ?? []
      const snapshots = Array.from(
        document.querySelectorAll<HTMLElement>('[data-moldy-message-role="user"]'),
      ).map((element) => {
        const id = element.dataset.moldyMessageId ?? 'no-id'
        return `${id}:${element.innerText.replace(/\s+/g, ' ').trim()}`
      })
      const visibleTexts = snapshots.join('\n')
      const allVisible = expectedTexts.every((text) => visibleTexts.includes(text))
      if (allVisible) {
        observedWindow.__moldyUserTextStabilityReadyAt ??= performance.now()
      } else if (typeof observedWindow.__moldyUserTextStabilityReadyAt === 'number') {
        observedWindow.__moldyUserTextFlickerFrames?.push(
          `${window.location.pathname}\n${snapshots.join('\n')}`,
        )
      }
      const readyAt = observedWindow.__moldyUserTextStabilityReadyAt
      return typeof readyAt === 'number' && performance.now() - readyAt >= stableDurationMs
    },
    durationMs,
    { timeout: durationMs + 5000, polling: 50 },
  )
  const frames = await page.evaluate(() => {
    const observedWindow = window as EmptyStateObserverWindow
    return observedWindow.__moldyUserTextFlickerFrames ?? []
  })
  expect(frames, `user message text flickered while streaming:\n${frames.join('\n---\n')}`).toEqual(
    [],
  )
}

async function expectAssistantTextOccurrenceCount(
  assistantMessage: ReturnType<Page['locator']>,
  pattern: RegExp,
  expectedCount: number,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const text = (await assistantMessage.last().innerText()).replace(/\s+/g, ' ')
        return Array.from(text.matchAll(pattern)).length
      },
      { timeout: 10_000, intervals: [100, 250, 500] },
    )
    .toBe(expectedCount)
}

interface BranchPickerSnapshot {
  readonly label: string | null
  readonly rowOpacity: number | null
}

async function branchPickerSnapshot(
  message: ReturnType<Page['locator']>,
): Promise<BranchPickerSnapshot> {
  return message.last().evaluate((element) => {
    const previousButton = element.querySelector<HTMLButtonElement>(
      'button[aria-label="이전 분기"]',
    )
    const picker = previousButton?.parentElement
    const row = picker?.parentElement
    const label = picker?.textContent?.replace(/\s+/g, '').match(/\d+\/\d+/)?.[0] ?? null
    return {
      label,
      rowOpacity: row instanceof HTMLElement ? Number(window.getComputedStyle(row).opacity) : null,
    }
  })
}

async function expectBranchPickerVisible(
  message: ReturnType<Page['locator']>,
  expectedLabel: string,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const snapshot = await branchPickerSnapshot(message)
        return snapshot.label === expectedLabel ? (snapshot.rowOpacity ?? 0) : 0
      },
      { timeout: 20_000, intervals: [100, 250, 500] },
    )
    .toBeGreaterThan(0.9)
}

async function expectBranchPickerVisibleWhileRunning(
  message: ReturnType<Page['locator']>,
  expectedLabel: string,
  incompleteUntilText: RegExp,
): Promise<void> {
  await expect
    .poll(
      async () => {
        const snapshot = await branchPickerSnapshot(message)
        const text = await message
          .last()
          .innerText({ timeout: 500 })
          .catch(() => '')
        return (
          snapshot.label === expectedLabel &&
          (snapshot.rowOpacity ?? 0) > 0.9 &&
          !incompleteUntilText.test(text)
        )
      },
      { timeout: 10_000, intervals: [100, 250, 500] },
    )
    .toBe(true)
}

test.describe('LangGraph v3 draft conversation lifecycle', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(
    process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy',
    'Skipped for the legacy chat runtime',
  )

  test('bootstraps a hidden SDK draft thread and deletes it when abandoned', async ({
    page,
    request,
    errors,
  }) => {
    const setup = await setupLangGraphV3Agent(request)
    const draftConversationPosts: string[] = []
    const startRequests: string[] = []
    const draftConversationIds = watchDraftConversationIds(page, setup.parentAgentId)
    page.on('request', (req) => {
      const url = req.url()
      if (
        req.method() !== 'POST' ||
        !url.includes(`/api/agents/${setup.parentAgentId}/conversations`)
      ) {
        return
      }
      if (url.endsWith('/start')) {
        startRequests.push(url)
        return
      }
      if (url.endsWith('/draft')) draftConversationPosts.push(url)
    })

    try {
      const beforeIds = await listConversationIds(request, setup.parentAgentId)
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await page.getByRole('button', { name: '새 채팅', exact: true }).first().click()
      await page.waitForURL(`**/agents/${setup.parentAgentId}/conversations/new`, {
        timeout: 10_000,
      })

      await expect
        .poll(async () => draftConversationIds.length, {
          timeout: 10_000,
        })
        .toBe(1)
      const afterIds = await listConversationIds(request, setup.parentAgentId)
      expect(afterIds).toEqual(beforeIds)
      await expect(page).toHaveURL(new RegExp(`/agents/${setup.parentAgentId}/conversations/new$`))
      expect(draftConversationPosts).toHaveLength(1)
      expect(startRequests).toEqual([])
      await expect(page.getByPlaceholder('메시지 입력...')).toBeVisible({ timeout: 20_000 })

      await page.goto(`/agents/${setup.parentAgentId}/settings`)
      await expectConversationDetailStatus(request, draftConversationIds[0] ?? '', 404)

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('creates a fresh hidden SDK draft thread when re-entering draft for the same agent', async ({
    page,
    request,
    errors,
  }) => {
    const setup = await setupLangGraphV3Agent(request)
    const draftConversationPosts: string[] = []
    const startRequests: string[] = []
    const draftConversationIds = watchDraftConversationIds(page, setup.parentAgentId)
    page.on('request', (req) => {
      const url = req.url()
      if (
        req.method() !== 'POST' ||
        !url.includes(`/api/agents/${setup.parentAgentId}/conversations`)
      ) {
        return
      }
      if (url.endsWith('/start')) {
        startRequests.push(url)
        return
      }
      if (url.endsWith('/draft')) draftConversationPosts.push(url)
    })

    try {
      const beforeIds = await listConversationIds(request, setup.parentAgentId)
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await page.getByRole('button', { name: '새 채팅', exact: true }).first().click()
      await page.waitForURL(`**/agents/${setup.parentAgentId}/conversations/new`, {
        timeout: 10_000,
      })

      await expect
        .poll(async () => draftConversationIds.length, {
          timeout: 10_000,
        })
        .toBe(1)
      const firstDraftId = draftConversationIds[0] ?? ''
      expect(await listConversationIds(request, setup.parentAgentId)).toEqual(beforeIds)
      await expect(page).toHaveURL(new RegExp(`/agents/${setup.parentAgentId}/conversations/new$`))

      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await expectConversationDetailStatus(request, firstDraftId, 404)
      await page.getByRole('button', { name: '새 채팅', exact: true }).first().click()
      await page.waitForURL(`**/agents/${setup.parentAgentId}/conversations/new`, {
        timeout: 10_000,
      })

      await expect
        .poll(async () => draftConversationIds.length, {
          timeout: 10_000,
        })
        .toBe(2)
      const secondDraftId = draftConversationIds[1] ?? ''
      expect(secondDraftId).not.toBe(firstDraftId)
      expect(await listConversationIds(request, setup.parentAgentId)).toEqual(beforeIds)
      await expect(page).toHaveURL(new RegExp(`/agents/${setup.parentAgentId}/conversations/new$`))
      expect(draftConversationPosts).toHaveLength(2)
      expect(startRequests).toEqual([])
      await expect(page.getByPlaceholder('메시지 입력...')).toBeVisible({ timeout: 20_000 })

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('promotes the hidden draft after the first message without restoring the opener', async ({
    page,
    request,
    errors,
  }) => {
    const setup = await setupLangGraphV3Agent(request)

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await page.getByRole('button', { name: '새 채팅', exact: true }).first().click()
      await page.waitForURL(`**/agents/${setup.parentAgentId}/conversations/new`, {
        timeout: 10_000,
      })
      await expect(page.getByText(EMPTY_STATE_TEXT)).toBeVisible({ timeout: 20_000 })

      const prompt = `안녕? E2E_FIRST_TURN_FLICKER_${Date.now()}`
      await installEmptyStateReappearanceObserver(page, prompt)
      await installMessageDisappearanceObserver(page, prompt, FIRST_TURN_RESPONSE_TEXT)
      await sendMessage(page, prompt)

      await expect(page).toHaveURL(
        new RegExp(`/agents/${setup.parentAgentId}/conversations/(?!new$)[^/]+$`),
        { timeout: 5_000 },
      )
      const promotedConversationId = new URL(page.url()).pathname.split('/').at(-1) ?? ''
      await expectConversationDetailStatus(request, promotedConversationId, 200)
      await expect
        .poll(async () => listConversationIds(request, setup.parentAgentId), {
          timeout: 10_000,
          intervals: [250, 500, 1000],
        })
        .toContain(promotedConversationId)
      await expect(page.getByText(FIRST_TURN_RESPONSE_TEXT).last()).toBeVisible({
        timeout: 30_000,
      })
      await expect(page).toHaveURL(
        new RegExp(`/agents/${setup.parentAgentId}/conversations/${promotedConversationId}$`),
        { timeout: 20_000 },
      )
      await expect(page.getByText(EMPTY_STATE_TEXT)).toHaveCount(0)
      await expectNoEmptyStateReappearance(page)
      await expectNoMessageDisappearance(page)

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('promotes the draft navigator row as soon as the first response starts streaming', async ({
    page,
    request,
    errors,
  }) => {
    const setup = await setupLangGraphV3Agent(request)
    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      await page.getByRole('button', { name: '새 채팅', exact: true }).first().click()
      await page.waitForURL(`**/agents/${setup.parentAgentId}/conversations/new`, {
        timeout: 10_000,
      })

      const prompt = `E2E_VISUAL_SLOW_STREAM E2E_DRAFT_NAV_${Date.now()}`
      await sendMessage(page, prompt)
      await expect(page.getByText(/E2E visual stream fixture is still running/).last()).toBeVisible(
        {
          timeout: 10_000,
        },
      )

      await expect(page).toHaveURL(
        new RegExp(`/agents/${setup.parentAgentId}/conversations/(?!new$)[^/]+$`),
        { timeout: 1_000 },
      )
      const promotedConversationId = new URL(page.url()).pathname.split('/').at(-1) ?? ''
      await expectConversationDetailStatus(request, promotedConversationId, 200)
      await waitRunActive(request, promotedConversationId)

      await expect(
        page.locator(`[data-chat-session-href="/agents/${setup.parentAgentId}/conversations/new"]`),
      ).toHaveCount(0)
      const promotedRow = page.locator(
        `[data-chat-session-href="/agents/${setup.parentAgentId}/conversations/${promotedConversationId}"]`,
      )
      await expect(promotedRow).toBeVisible({ timeout: 5_000 })
      await expect(promotedRow).toHaveClass(/bg-primary/)

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('keeps the second streamed assistant reply visible after it settles', async ({
    page,
    request,
    errors,
  }) => {
    const setup = await setupLangGraphV3Agent(request)

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const firstPrompt = `안녕? E2E_SECOND_TURN_FIRST_${Date.now()}`
      await sendMessage(page, firstPrompt)
      await expect(page.getByText(FIRST_TURN_RESPONSE_TEXT).last()).toBeVisible({
        timeout: 30_000,
      })
      await waitRunIdle(request, setup.conversationId)
      await waitForNoVisibleStopButton(page)

      const secondPrompt = `반가워 E2E_SECOND_TURN_${Date.now()}`
      await sendMessage(page, secondPrompt)
      await expect(page.getByText(secondPrompt)).toBeVisible({ timeout: 10_000 })
      const assistantMessages = page.locator('[data-moldy-message-role="assistant"]').filter({
        hasText: FIRST_TURN_RESPONSE_TEXT,
      })
      await expect(assistantMessages).toHaveCount(2, { timeout: 30_000 })
      await waitRunIdle(request, setup.conversationId)
      await expectAssistantMessageCountStable(page, FIRST_TURN_RESPONSE_TEXT, 2)

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('keeps earlier user message text visible while a later assistant reply streams', async ({
    page,
    request,
    errors,
  }) => {
    const setup = await setupLangGraphV3Agent(request)

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const unique = Date.now()
      const firstPrompt = `안녕? E2E_USER_TEXT_FIRST_${unique}`
      const secondPrompt = `반가워 E2E_VISUAL_SLOW_STREAM E2E_USER_TEXT_SECOND_${unique}`

      await sendMessage(page, firstPrompt)
      await expect(page.getByText(FIRST_TURN_RESPONSE_TEXT).last()).toBeVisible({
        timeout: 30_000,
      })
      await waitRunIdle(request, setup.conversationId)
      await waitForNoVisibleStopButton(page)

      await sendMessage(page, secondPrompt)
      const userMessages = page.locator('[data-moldy-message-role="user"]')
      await expect(userMessages.filter({ hasText: firstPrompt })).toHaveCount(1, {
        timeout: 10_000,
      })
      await expect(userMessages.filter({ hasText: secondPrompt })).toHaveCount(1, {
        timeout: 10_000,
      })
      await installUserTextStabilityObserver(page, [firstPrompt, secondPrompt])
      await expect(page.getByText(/E2E visual stream fixture is still running/).last()).toBeVisible(
        {
          timeout: 10_000,
        },
      )
      const wittyLoading = page.locator('[data-moldy-witty-loading="true"]')
      await expect(wittyLoading.last()).toBeVisible({ timeout: 10_000 })
      await expectNoUserTextFlicker(page)
      await waitRunIdle(request, setup.conversationId)
      await expect(wittyLoading).toHaveCount(0, { timeout: 10_000 })
      await expect(userMessages.filter({ hasText: firstPrompt })).toHaveCount(1)
      await expect(userMessages.filter({ hasText: secondPrompt })).toHaveCount(1)

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('edits a user message without rendering a duplicate optimistic user bubble', async ({
    page,
    request,
    errors,
  }) => {
    const setup = await setupLangGraphV3Agent(request)

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const prompt = `E2E_EDIT_ORIGINAL_${Date.now()}`
      const editedPrompt = `E2E_SLOW_STREAM E2E_EDITED_${Date.now()}`
      await sendMessage(page, prompt)
      await expect(page.getByText(FIRST_TURN_RESPONSE_TEXT).last()).toBeVisible({
        timeout: 30_000,
      })

      const userMessage = page.locator('[data-moldy-message-role="user"]').filter({
        hasText: prompt,
      })
      await expect(userMessage).toHaveCount(1, { timeout: 20_000 })
      await userMessage.last().hover()
      await userMessage.last().getByRole('button', { name: '편집' }).click()

      const editInput = page.locator('textarea:not([data-moldy-composer-input="true"])').last()
      await expect(editInput).toBeVisible({ timeout: 10_000 })
      await editInput.fill(editedPrompt)
      await installEditedUserDuplicateObserver(page, editedPrompt)
      await installStaleAssistantAfterEditObserver(page, editedPrompt, FIRST_TURN_RESPONSE_TEXT)
      await page.getByRole('button', { name: '저장' }).click()

      const editedUserMessage = page.locator('[data-moldy-message-role="user"]').filter({
        hasText: editedPrompt,
      })
      await expect(editedUserMessage).toHaveCount(1, { timeout: 20_000 })
      await expectNoEditedUserDuplicate(page)
      await expectNoStaleAssistantAfterEdit(page)
      await expect(
        page.getByText(/E2E slow\s+stream\s+completed\s+after\s+detached\s+navigation\./).last(),
      ).toBeVisible({ timeout: 30_000 })
      const editedAssistantMessage = page.locator('[data-moldy-message-role="assistant"]').filter({
        hasText: /E2E slow\s+stream\s+completed\s+after\s+detached\s+navigation\./,
      })
      await expect(editedAssistantMessage.last()).not.toContainText(/2\/2/)

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })

  test('keeps the newest user edit branch active when regenerating after multiple edits', async ({
    page,
    request,
    errors,
  }) => {
    test.setTimeout(180_000)
    const setup = await setupLangGraphV3Agent(request)

    try {
      await expectConversationDetailStatus(request, setup.conversationId, 200)
      await expectConversationMessagesStatus(request, setup.conversationId, 200)
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const unique = Date.now()
      const originalPrompt = `안녕? E2E_BRANCH_ORIGINAL_${unique}`
      const secondPrompt = `바보 E2E_SLOW_STREAM E2E_BRANCH_SECOND_${unique}`
      const thirdPrompt = `반가워 E2E_VISUAL_SLOW_STREAM E2E_BRANCH_THIRD_${unique}`

      await sendMessage(page, originalPrompt)
      await expect(page.getByText(FIRST_TURN_RESPONSE_TEXT).last()).toBeVisible({
        timeout: 30_000,
      })

      await editVisibleUserMessage(page, originalPrompt, secondPrompt)
      await expect(
        page.getByText(/E2E slow\s+stream\s+completed\s+after\s+detached\s+navigation\./).last(),
      ).toBeVisible({ timeout: 30_000 })

      const secondUserMessage = page.locator('[data-moldy-message-role="user"]').filter({
        hasText: secondPrompt,
      })
      await expect(secondUserMessage).toHaveCount(1, { timeout: 20_000 })
      await secondUserMessage.last().hover()
      await secondUserMessage.last().getByRole('button', { name: '편집' }).click()
      const thirdEditInput = page.locator('textarea:not([data-moldy-composer-input="true"])').last()
      await expect(thirdEditInput).toBeVisible({ timeout: 10_000 })
      await thirdEditInput.fill(thirdPrompt)
      await installWrongBranchIndexObserver(page, thirdPrompt, '2/3')
      await page.getByRole('button', { name: '저장' }).click()
      const visualFinalText = /chunks\s+arrive;\s+visual\s+stream\s+fixture\s+complete\./
      const visualPartialText = /E2E visual stream fixture is still running/
      await expect(page.getByText(visualFinalText).last()).toBeVisible({ timeout: 30_000 })
      await expectNoWrongBranchIndex(page)

      const newestUserMessage = page.locator('[data-moldy-message-role="user"]').filter({
        hasText: thirdPrompt,
      })
      await expect(newestUserMessage).toHaveCount(1, { timeout: 20_000 })
      await expect(newestUserMessage.last()).toContainText(/3\/3/, { timeout: 20_000 })
      await newestUserMessage.last().hover()
      await expectBranchPickerVisible(newestUserMessage, '3/3')

      await newestUserMessage.last().getByRole('button', { name: '이전 분기' }).click()
      const previousUserBranch = page.locator('[data-moldy-message-role="user"]').filter({
        hasText: secondPrompt,
      })
      await expect(previousUserBranch).toHaveCount(1, { timeout: 20_000 })
      await expectBranchPickerVisible(previousUserBranch, '2/3')
      await previousUserBranch.last().getByRole('button', { name: '다음 분기' }).click()
      await expect(newestUserMessage).toHaveCount(1, { timeout: 20_000 })
      await expectBranchPickerVisible(newestUserMessage, '3/3')

      const newestAssistantMessage = page.locator('[data-moldy-message-role="assistant"]').filter({
        hasText: visualFinalText,
      })
      const waitForVisualAssistantComplete = async () => {
        await waitForVisibleStopButton(page)
        await waitRunIdle(request, setup.conversationId)
        await expect(page.getByText(visualFinalText).last()).toBeVisible({ timeout: 30_000 })
        await waitForNoVisibleStopButton(page)
        await expect(
          newestAssistantMessage.last().getByRole('button', { name: '재생성' }),
        ).toBeEnabled({ timeout: 30_000 })
        await newestAssistantMessage.last().hover()
      }
      await expect(newestAssistantMessage.last()).not.toContainText(/3\/3/)
      await expectAssistantTextOccurrenceCount(
        newestAssistantMessage,
        /visual stream fixture complete\./g,
        1,
      )
      await newestAssistantMessage.last().hover()
      await newestAssistantMessage.last().getByRole('button', { name: '재생성' }).click()

      await waitForVisibleStopButton(page)
      const streamingAssistantMessage = page
        .locator('[data-moldy-message-role="assistant"]')
        .filter({
          hasText: visualPartialText,
        })
      await expect(streamingAssistantMessage.last()).toBeVisible({ timeout: 10_000 })
      await streamingAssistantMessage.last().hover()
      await expectBranchPickerVisibleWhileRunning(streamingAssistantMessage, '2/2', visualFinalText)
      await waitForVisualAssistantComplete()
      await expect(page.getByText(FIRST_TURN_RESPONSE_TEXT)).toHaveCount(0)
      await expect(newestUserMessage.last()).toContainText(/3\/3/, { timeout: 20_000 })
      await expectBranchPickerVisible(newestAssistantMessage, '2/2')
      await newestAssistantMessage.last().getByRole('button', { name: '재생성' }).click()
      await waitForVisualAssistantComplete()
      await expectBranchPickerVisible(newestAssistantMessage, '3/3')
      await newestAssistantMessage.last().getByRole('button', { name: '재생성' }).click()
      await waitForVisualAssistantComplete()
      await expectBranchPickerVisible(newestAssistantMessage, '4/4')
      await newestAssistantMessage.last().getByRole('button', { name: '이전 분기' }).click()
      await expectBranchPickerVisible(newestAssistantMessage, '3/4')
      await newestAssistantMessage.last().getByRole('button', { name: '재생성' }).click()
      await waitForVisualAssistantComplete()
      await expectBranchPickerVisible(newestAssistantMessage, '5/5')
      await expectAssistantTextOccurrenceCount(
        newestAssistantMessage,
        /visual stream fixture complete\./g,
        1,
      )

      expect(errors.console).toEqual([])
      expect(errors.network).toEqual([])
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })
})
