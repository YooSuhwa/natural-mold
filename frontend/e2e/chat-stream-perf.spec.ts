import { API_BASE, apiDeleteOk, expect, test } from './fixtures'
import { sendMessage, setupLangGraphV3Agent } from './langgraph-v3-helpers'

/**
 * Streaming PERFORMANCE tripwire (text-only E2E_SLOW_STREAM: 6 chunks x 0.75s
 * server-side = ~4.5s spread). Not a tight benchmark — the shared checkpointer
 * pool makes absolute latency vary (see repo CLAUDE.md). Instead this guards two
 * coarse, meaningful properties and records the measured numbers:
 *   1. no hang / gross slowdown (generous upper bounds on TTFT + total)
 *   2. the stream renders PROGRESSIVELY, not buffered-and-dumped at the end
 *      (the first chunk must become visible well before the last).
 *
 * Run with --workers=1 to keep the shared-pool variance down.
 */
test.describe('Chat streaming performance', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')
  test.skip(process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy', 'Skipped for the legacy chat runtime')

  test('streams progressively within sane time bounds (no buffering bottleneck)', async ({
    page,
    request,
  }, testInfo) => {
    test.setTimeout(120_000)
    const setup = await setupLangGraphV3Agent(request)
    // The assistant message starts with chunk 1 ("E2E slow ") and ends with the
    // full sentence; ``firstChunk`` matches as soon as streaming begins, ``final``
    // only once the last chunk lands. The prompt uses "E2E_SLOW_STREAM" (underscore)
    // so it never matches the lowercase "E2E slow" chunk text.
    const firstChunk = page.getByText('E2E slow', { exact: false }).last()
    const final = page.getByText('E2E slow stream completed after detached navigation.').last()

    try {
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const t0 = performance.now()
      await sendMessage(page, 'E2E_SLOW_STREAM 성능 측정')

      await expect(firstChunk).toBeVisible({ timeout: 20_000 })
      const ttftMs = performance.now() - t0

      await expect(final).toBeVisible({ timeout: 40_000 })
      const totalMs = performance.now() - t0

      const spreadMs = totalMs - ttftMs
      testInfo.annotations.push({
        type: 'perf',
        description: `TTFT=${Math.round(ttftMs)}ms total=${Math.round(totalMs)}ms spread=${Math.round(spreadMs)}ms`,
      })

      // Tripwire bounds (catch hangs, not micro-regressions).
      expect(ttftMs, `time-to-first-token too slow: ${Math.round(ttftMs)}ms`).toBeLessThan(20_000)
      expect(totalMs, `total stream too slow: ${Math.round(totalMs)}ms`).toBeLessThan(40_000)
      // Progressive rendering: the 6x0.75s stream must spread over time, not
      // appear all at once (which would signal a buffering bottleneck).
      expect(
        spreadMs,
        `stream not progressive (first->last spread only ${Math.round(spreadMs)}ms)`,
      ).toBeGreaterThan(1500)
    } finally {
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.parentAgentId}`, setup.csrfHeaders)
      await apiDeleteOk(request, `${API_BASE}/api/agents/${setup.childAgentId}`, setup.csrfHeaders)
    }
  })
})
