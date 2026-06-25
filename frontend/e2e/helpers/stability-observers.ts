import type { Page } from '@playwright/test'
import { expect } from '../fixtures'

/**
 * Reusable DOM-stability observers for chat E2E specs.
 *
 * These wrap a `MutationObserver` (injected via `page.evaluate`) that records a
 * "ready" timestamp once a target condition first holds, then captures any
 * later regression of that condition. `expectNo*` then polls for a stable
 * window (no regressions for `durationMs`) and asserts nothing was captured.
 *
 * The mechanism is lifted from the proven inline observers in
 * `draft-conversation-langgraph-v3.spec.ts`; that spec keeps its own copies for
 * now and should migrate to this helper in a follow-up. Window keys are
 * namespaced (`__moldySh*`) so importing both never collides.
 */

type StabilityWindow = Window & {
  __moldyShUserTextObserver?: MutationObserver
  __moldyShUserTextReadyAt?: number
  __moldyShUserTextFrames?: string[]
  __moldyShUserTextExpected?: string[]
}

/**
 * Start observing that every text in `texts` (a user message) STAYS visible.
 * Call before the action that streams an assistant reply, then assert with
 * {@link expectNoUserTextFlicker}.
 */
export async function installUserTextStabilityObserver(
  page: Page,
  texts: readonly string[],
): Promise<void> {
  await page.evaluate((expectedTexts) => {
    const w = window as StabilityWindow
    w.__moldyShUserTextObserver?.disconnect()
    w.__moldyShUserTextReadyAt = undefined
    w.__moldyShUserTextFrames = []
    w.__moldyShUserTextExpected = [...expectedTexts]

    const snapshots = (): string[] =>
      Array.from(
        document.querySelectorAll<HTMLElement>('[data-moldy-message-role="user"]'),
      ).map((element) => {
        const id = element.dataset.moldyMessageId ?? 'no-id'
        return `${id}:${element.innerText.replace(/\s+/g, ' ').trim()}`
      })

    const check = () => {
      const frame = snapshots()
      const joined = frame.join('\n')
      const allVisible = expectedTexts.every((text) => joined.includes(text))
      if (allVisible) {
        w.__moldyShUserTextReadyAt ??= performance.now()
        return
      }
      if (typeof w.__moldyShUserTextReadyAt !== 'number') return
      w.__moldyShUserTextFrames?.push(`${window.location.pathname}\n${joined}`)
    }

    const observer = new MutationObserver(check)
    observer.observe(document.body, { childList: true, subtree: true, characterData: true })
    w.__moldyShUserTextObserver = observer
    check()
  }, texts)
}

/**
 * Assert the user message text never disappeared/flickered for `durationMs`
 * after first becoming fully visible. Fails with the captured frames.
 */
export async function expectNoUserTextFlicker(page: Page, durationMs = 1500): Promise<void> {
  await page.waitForFunction(
    (stableDurationMs) => {
      const w = window as StabilityWindow
      const expectedTexts = w.__moldyShUserTextExpected ?? []
      const frame = Array.from(
        document.querySelectorAll<HTMLElement>('[data-moldy-message-role="user"]'),
      ).map((element) => {
        const id = element.dataset.moldyMessageId ?? 'no-id'
        return `${id}:${element.innerText.replace(/\s+/g, ' ').trim()}`
      })
      const joined = frame.join('\n')
      const allVisible = expectedTexts.every((text) => joined.includes(text))
      if (allVisible) {
        w.__moldyShUserTextReadyAt ??= performance.now()
      } else if (typeof w.__moldyShUserTextReadyAt === 'number') {
        w.__moldyShUserTextFrames?.push(`${window.location.pathname}\n${joined}`)
      }
      const readyAt = w.__moldyShUserTextReadyAt
      return typeof readyAt === 'number' && performance.now() - readyAt >= stableDurationMs
    },
    durationMs,
    { timeout: durationMs + 8000, polling: 50 },
  )
  const frames = await page.evaluate(() => {
    const w = window as StabilityWindow
    return w.__moldyShUserTextFrames ?? []
  })
  expect(frames, `user message text flickered while streaming:\n${frames.join('\n---\n')}`).toEqual(
    [],
  )
}
