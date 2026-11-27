// From the repo root: pnpm --dir frontend exec node --test contracts/ag-ui-v1-stream.contract.mjs
// The only model is a local deterministic test double. HttpAgent reads the Python encoder's
// SSE response via its ordinary HTTP transport with fetch replaced at the network boundary.
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { before, test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { HttpAgent, verifyEvents } from '@ag-ui/client'
import { EventSchema } from '@ag-ui/core/schemas'
import { from, lastValueFrom, toArray } from 'rxjs'

let corpus

before(() => {
  const json = execFileSync(
    'uv',
    [
      'run',
      '--frozen',
      '--all-extras',
      'python',
      '-m',
      'tests.agent_runtime.ag_ui_v1_contract_cases',
    ],
    {
      cwd: fileURLToPath(new URL('../../backend/', import.meta.url)),
      encoding: 'utf8',
      timeout: 120_000,
      env: { ...process.env, LANGCHAIN_TRACING_V2: 'false', LANGSMITH_TRACING: 'false' },
    },
  )
  corpus = JSON.parse(json)
})

function parse(wire) {
  return wire
    .trimEnd()
    .split(/\r?\n\r?\n/)
    .map((block) => {
      const payload = block
        .split(/\r?\n/)
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n')
      return EventSchema.parse(JSON.parse(payload))
    })
}

async function verify(wire) {
  const events = parse(wire)
  await lastValueFrom(from(events).pipe(verifyEvents(), toArray()))
  assert.equal(events.filter((e) => ['RUN_FINISHED', 'RUN_ERROR'].includes(e.type)).length, 1)
  assert.ok(['RUN_FINISHED', 'RUN_ERROR'].includes(events.at(-1).type))
  return events
}

function response(wire) {
  const bytes = new TextEncoder().encode(wire)
  const body = new ReadableStream({
    start(controller) {
      // The SSE decoder also sees chunk boundaries inside UTF-8 characters and JSON.
      for (let i = 0; i < bytes.length; i += 31) controller.enqueue(bytes.subarray(i, i + 31))
      controller.close()
    },
  })
  return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
}

async function consume(wire) {
  const events = await verify(wire)
  let requests = 0
  const endings = []
  const agent = new HttpAgent({
    url: 'https://fixture.invalid/run',
    threadId: corpus.threadId,
    fetch: async () => {
      requests += 1
      return response(wire)
    },
  })
  await agent.runAgent(
    { runId: corpus.runId },
    {
      onRunFinishedEvent: ({ outcome }) => {
        endings.push(outcome)
      },
      onRunErrorEvent: () => {
        endings.push('error')
      },
    },
  )
  assert.equal(requests, 1)
  assert.equal(endings.length, 1)
  return { events, messages: agent.messages, state: agent.state, outcome: endings[0] }
}

function ofType(events, type) {
  return events.filter((event) => event.type === type)
}

test('the official HTTP consumer restores the same delegation from archived and current journals', async () => {
  const views = []
  for (const name of ['archived', 'installed']) {
    const { events, messages, outcome } = await consume(corpus.streams[name])
    assert.equal(outcome, 'success')
    assert.equal(events[0].type, 'RUN_STARTED')
    assert.equal(events[0].protocolVersion, '1.0')
    assert.equal(events[0].runId, corpus.runId)
    assert.equal(events[0].threadId, corpus.threadId)
    const starts = ofType(events, 'SUBAGENT_STARTED')
    assert.equal(starts.length, 1)
    const [child] = starts
    assert.equal(child.name, 'researcher')
    assert.equal(child.parentToolCallId, 'call-research')
    assert.deepEqual(
      ofType(events, 'SUBAGENT_FINISHED').map((e) => e.subagentRunId),
      [child.subagentRunId],
    )
    for (const type of ['TOOL_CALL_START', 'TOOL_CALL_END', 'TOOL_CALL_RESULT']) {
      assert.deepEqual(
        ofType(events, type).map((e) => e.toolCallId),
        ['call-research'],
      )
    }
    const result = messages.find((m) => m.role === 'tool' && m.toolCallId === 'call-research')
    assert.equal(result?.content, '대상은 세 곳입니다.')
    const first = messages.find((m) => m.id === 'boss-task')
    assert.equal(first?.toolCalls[0]?.id, 'call-research')
    const root = messages.filter((m) => m.role === 'assistant' && !m.subagentRunId)
    assert.deepEqual(
      root.map((m) => m.id),
      ['boss-task', 'boss-final'],
    )
    assert.deepEqual(
      root.map((m) => m.content),
      ['위임합니다.', '조사를 마쳤습니다.'],
    )
    const owned = messages.filter(
      (m) => m.role === 'assistant' && m.subagentRunId === child.subagentRunId,
    )
    assert.deepEqual(
      owned.map((m) => m.content),
      ['대상은 세 곳입니다.'],
    )
    views.push([root.map(({ id, content }) => [id, content]), owned[0].content, result.content])
  }
  assert.deepEqual(views[0], views[1])
})

test('interleaved siblings have separate message owners even when model IDs coincide', async () => {
  const { messages, events } = await consume(corpus.streams.siblings)
  const byCall = new Map(
    ofType(events, 'SUBAGENT_STARTED').map((e) => [e.parentToolCallId, e.subagentRunId]),
  )
  assert.equal(new Set(byCall.values()).size, 2)
  for (const [callId, expected] of [
    ['call-a', '첫째 응답'],
    ['call-b', '둘째 응답'],
  ]) {
    const child = messages.filter(
      (m) => m.role === 'assistant' && m.subagentRunId === byCall.get(callId),
    )
    assert.deepEqual(
      child.map((m) => m.content),
      [expected],
    )
  }
  assert.equal(messages.find((m) => m.id === 'last-answer')?.content, '마침')
  assert.doesNotMatch(corpus.streams.siblings, /PRIVATE_(STATE|FRAME|REASONING)_CANARY|rawEvent/)
})

test('a restricted replay omits revoked child messages, briefs and tool results', async () => {
  const wire = corpus.streams.restricted
  const { events } = await consume(wire)
  assert.deepEqual(
    ofType(events, 'SUBAGENT_STARTED').map((e) => e.parentToolCallId),
    ['call-b'],
  )
  assert.deepEqual(
    ofType(events, 'TOOL_CALL_RESULT').map((e) => e.toolCallId),
    ['call-b'],
  )
  assert.doesNotMatch(wire, /REVOKED_(BRIEF|RESULT)|첫째|call-a|PRIVATE_[A-Z_]+_CANARY/)
})

test('the official client applies state and activity replacements and counts model usage once', async () => {
  const wire = corpus.streams.activities
  const { events, messages, state } = await consume(wire)
  assert.deepEqual(state, { todos: [{ id: 'todo-1', content: '정리', status: 'completed' }] })
  const activity = (name) =>
    messages.filter((m) => m.role === 'activity' && m.activityType === name)
  const updates = (name) =>
    ofType(events, 'ACTIVITY_SNAPSHOT').filter((e) => e.activityType === name)
  const steer = updates('moldy.run_steer')
  assert.deepEqual(
    steer.map((e) => e.content.status),
    ['pending', 'applied'],
  )
  assert.equal(steer[0].messageId, steer[1].messageId)
  assert.deepEqual(
    activity('moldy.run_steer').map((m) => m.content.status),
    ['applied'],
  )
  assert.deepEqual(
    activity('moldy.compaction').map((m) => m.content.state),
    ['done'],
  )
  assert.deepEqual(
    activity('moldy.memory_recalled').map((m) => m.content.memories[0].content),
    ['<redacted>'],
  )
  assert.deepEqual(
    activity('moldy.usage').map((m) => m.content.public_message_id),
    ['billed-message'],
  )
  const usage = events.at(-1).usage
  assert.deepEqual(
    usage.map(({ inputTokens, outputTokens, totalTokens }) => [
      inputTokens,
      outputTokens,
      totalTokens,
    ]),
    [[9, 3, 12]],
  )
  assert.doesNotMatch(wire, /PRIVATE_(SUMMARY|MEMORY|STATE)_CANARY/)
})

test('two approvals can resume a separate run on the same official agent', async () => {
  const [first, next] = await Promise.all([
    verify(corpus.streams.approval),
    verify(corpus.streams.resumed),
  ])
  const requests = []
  const outcomes = []
  const agent = new HttpAgent({
    url: 'https://fixture.invalid/run',
    threadId: corpus.threadId,
    fetch: async (_url, options) => {
      requests.push(JSON.parse(options.body))
      return response(requests.length === 1 ? corpus.streams.approval : corpus.streams.resumed)
    },
  })
  const subscriber = {
    onRunFinishedEvent: ({ outcome }) => {
      outcomes.push(outcome)
    },
  }
  await agent.runAgent({ runId: corpus.runId }, subscriber)
  const pending = first.at(-1).outcome.interrupts
  assert.deepEqual(pending.map((i) => i.id).sort(), ['confirm-first', 'confirm-second'])
  for (const interrupt of pending) assert.ok(interrupt.responseSchema)
  const resume = pending.map(({ id }) => ({
    interruptId: id,
    status: 'resolved',
    payload: { decisions: [{ type: 'approve' }] },
  }))
  await agent.runAgent({ runId: next[0].runId, resume }, subscriber)
  assert.deepEqual(outcomes, ['interrupt', 'success'])
  assert.equal(requests.length, 2)
  assert.equal(requests[0].threadId, requests[1].threadId)
  assert.notEqual(requests[0].runId, requests[1].runId)
  assert.deepEqual(requests[1].resume, resume)
})

test('cancellation and a first-frame error are distinct official terminal states', async () => {
  assert.equal((await consume(corpus.streams.cancelled)).outcome, 'cancelled')
  const { events, outcome } = await consume(corpus.streams.earlyError)
  assert.equal(outcome, 'error')
  assert.deepEqual(
    events.map((e) => e.type),
    ['RUN_ERROR'],
  )
  assert.doesNotMatch(corpus.streams.earlyError, /PRIVATE_ERROR_CANARY/)
})
