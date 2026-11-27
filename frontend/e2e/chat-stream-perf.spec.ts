import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { isAbsolute } from 'node:path'
import { API_BASE, apiDeleteOk, expect, isRecord, test } from './fixtures'
import { installStreamPerfTiming, readStreamPerfTiming } from './helpers/stream-perf-timing'
import { sendMessage, setupLangGraphV3Agent } from './langgraph-v3-helpers'

const fixtureOrdinals = [1, 2, 3, 4, 5, 6] as const

function backendTiming(conversationId: string, sampleId: string) {
  const errors: string[] = []
  let observed: Record<string, unknown> = {}
  const python = process.env.MOLDY_GATE_PYTHON
  const observer = process.env.MOLDY_AGUI_PERF_OBSERVER
  if (python && observer && isAbsolute(python) && isAbsolute(observer)) {
    try {
      const json = execFileSync(python, [observer, conversationId, sampleId], {
        encoding: 'utf8',
        timeout: 10_000,
        maxBuffer: 32_768,
        stdio: ['ignore', 'pipe', 'pipe'],
      })
      const value: unknown = JSON.parse(json)
      if (isRecord(value)) observed = value
      if (!Array.isArray(observed.errors) || observed.errors.length) {
        errors.push('backend_observer_reported_errors')
      }
    } catch {
      errors.push('backend_observer_failed')
    }
  } else {
    errors.push('backend_observer_unavailable')
  }

  // Attach six known numeric samples only. Never retain observer output or stderr.
  function at(source: unknown, ordinal: number, field: string): number | null {
    if (!Array.isArray(source)) return null
    const rows = source.filter((row) => isRecord(row) && row.ordinal === ordinal)
    if (rows.length !== 1 || !isRecord(rows[0])) return null
    const timestamp = rows[0][field]
    return typeof timestamp === 'number' && Number.isFinite(timestamp) && timestamp > 0
      ? timestamp
      : null
  }
  return {
    model: fixtureOrdinals.map((ordinal) => ({
      ordinal,
      sourceEpochMs: at(observed.model, ordinal, 'sourceEpochMs'),
    })),
    journal: fixtureOrdinals.map((ordinal) => ({
      ordinal,
      commitEpochMs: at(observed.journal, ordinal, 'commitEpochMs'),
    })),
    errors,
  }
}

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
  test.skip(
    process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'legacy',
    'Skipped for the legacy chat runtime',
  )

  test('streams progressively within sane time bounds (no buffering bottleneck)', async ({
    page,
    request,
  }, testInfo) => {
    test.setTimeout(120_000)
    const setup = await setupLangGraphV3Agent(request)
    const sampleId = process.env.E2E_AGUI_PERF_TIMING === '1' ? randomUUID() : null
    // The assistant message starts with chunk 1 ("E2E slow ") and ends with the
    // full sentence; ``firstChunk`` matches as soon as streaming begins, ``final``
    // only once the last chunk lands. The prompt uses "E2E_SLOW_STREAM" (underscore)
    // so it never matches the lowercase "E2E slow" chunk text.
    const firstChunk = page.getByText('E2E slow', { exact: false }).last()
    const final = page.getByText('E2E slow stream completed after detached navigation.').last()

    try {
      if (sampleId) await installStreamPerfTiming(page, API_BASE, setup.conversationId, sampleId)
      await page.goto(`/agents/${setup.parentAgentId}/conversations/${setup.conversationId}`)
      const t0 = performance.now()
      const prompt = sampleId
        ? `E2E_SLOW_STREAM E2E_AGUI_PERF_${sampleId} 성능 측정`
        : 'E2E_SLOW_STREAM 성능 측정'
      await sendMessage(page, prompt)

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
      try {
        if (sampleId) {
          const browser = (await readStreamPerfTiming(page).catch(() => null)) ?? {
            schemaVersion: 1,
            sampleId,
            conversationId: setup.conversationId,
            mode: 'unknown',
            publicRunId: null,
            physicalRunId: null,
            runIds: [],
            routes: [],
            submittedAtMs: null,
            firstFrameAtMs: null,
            lastFrameAtMs: null,
            ordinals: fixtureOrdinals.map((ordinal) => ({
              ordinal,
              domAtMs: null,
              networkAtMs: null,
            })),
            parserIssues: 1,
          }
          await testInfo.attach('perf-waterfall', {
            contentType: 'application/json',
            body: JSON.stringify({
              ...browser,
              expectedMode:
                process.env.NEXT_PUBLIC_CHAT_RUNTIME === 'ag_ui' ? 'ag_ui' : 'langgraph_v3',
              backendTiming: backendTiming(setup.conversationId, sampleId),
            }),
          })
        }
      } finally {
        await apiDeleteOk(
          request,
          `${API_BASE}/api/agents/${setup.parentAgentId}`,
          setup.csrfHeaders,
        )
        await apiDeleteOk(
          request,
          `${API_BASE}/api/agents/${setup.childAgentId}`,
          setup.csrfHeaders,
        )
      }
    }
  })
})
