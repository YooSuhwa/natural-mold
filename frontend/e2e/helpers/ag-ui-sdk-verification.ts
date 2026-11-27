import assert from 'node:assert/strict'

import { HttpAgent, parseSSEStream, runHttpRequest, verifyEvents } from '@ag-ui/client'
import { EventSchema, RunAgentInputSchema } from '@ag-ui/core/schemas'
import { from, lastValueFrom, map, toArray } from 'rxjs'

type RunOutcome = 'success' | 'interrupt' | 'cancelled' | 'error'

interface TranscriptExpectation {
  readonly sse: string
  readonly threadId: string
  readonly runId: string
  readonly outcome: RunOutcome
  readonly request?: unknown
  readonly assistantText?: string
  readonly interruptIds?: readonly string[]
  readonly inspectInterrupts?: (slots: readonly unknown[]) => void
  readonly forbiddenText?: readonly string[]
}

/** Decode the exact captured product response without making a second server request. */
export async function verifyAgUiTranscript(expected: TranscriptExpectation) {
  if (expected.request !== undefined) {
    const input = RunAgentInputSchema.parse(expected.request)
    assert.equal(input.runId, expected.runId)
    assert.equal(input.threadId, expected.threadId)
  }
  const response = () =>
    new Response(expected.sse, { headers: { 'content-type': 'text/event-stream; charset=utf-8' } })
  const raw = parseSSEStream(runHttpRequest(async () => response()))
  const events = await lastValueFrom(
    raw.pipe(
      map((frame: unknown) => EventSchema.parse(frame)),
      toArray(),
    ),
  )
  assert.ok(events.length, 'the HTTP request returned no AG-UI events')
  await lastValueFrom(from(events).pipe(verifyEvents(), toArray()))

  const first = events[0]
  if (first.type !== 'RUN_ERROR') {
    assert.equal(first.type, 'RUN_STARTED')
    assert.equal(first.threadId, expected.threadId)
    assert.equal(first.runId, expected.runId)
    assert.equal(first.protocolVersion, '1.0')
  }
  const starts = new Set<string>()
  const results = new Set<string>()
  for (const event of events) {
    assert.notEqual(event.type, 'RAW', 'public transcript includes a raw runtime event')
    assert.equal(event.rawEvent, undefined, 'public transcript includes a raw runtime payload')
    if (event.type === 'CUSTOM') assert.notEqual(event.name, 'moldy.raw_event')
    if (event.type === 'RUN_FINISHED') {
      assert.equal(event.runId, expected.runId)
      assert.equal(event.threadId, expected.threadId)
    }
    if (event.type === 'TOOL_CALL_START' || event.type === 'TOOL_CALL_RESULT') {
      const seen = event.type === 'TOOL_CALL_START' ? starts : results
      assert.ok(!seen.has(event.toolCallId), 'duplicate public tool event for the same call')
      seen.add(event.toolCallId)
    }
  }
  for (const sentinel of expected.forbiddenText ?? []) {
    assert.ok(!expected.sse.includes(sentinel), 'private fixture sentinel leaked into public SSE')
  }

  let outcome: RunOutcome | undefined
  let interruptIds: string[] = []
  let reads = 0
  const agent = new HttpAgent({
    url: 'https://capture.invalid/run',
    threadId: expected.threadId,
    fetch: async () => {
      reads++
      return response()
    },
  })
  await agent.runAgent(
    { runId: expected.runId },
    {
      onRunFinishedEvent(result) {
        outcome = result.outcome
        if (result.outcome === 'interrupt') {
          interruptIds = result.interrupts.map((interrupt) => interrupt.id)
          expected.inspectInterrupts?.(result.interrupts)
        }
      },
      onRunErrorEvent() {
        outcome = 'error'
      },
    },
  )
  assert.equal(reads, 1)
  assert.equal(
    outcome,
    expected.outcome,
    'the official SDK did not observe the expected terminal outcome',
  )
  if (expected.interruptIds) {
    assert.deepEqual([...interruptIds].sort(), [...expected.interruptIds].sort())
  }
  if (expected.assistantText !== undefined) {
    assert.ok(
      agent.messages.some(
        (message) => message.role === 'assistant' && message.content === expected.assistantText,
      ),
      'the official SDK did not reconstruct the visible assistant response',
    )
  }
  return {
    outcome,
    interruptIds,
    messageIds: agent.messages.map((message) => message.id),
    toolCallIds: [...starts],
  }
}
