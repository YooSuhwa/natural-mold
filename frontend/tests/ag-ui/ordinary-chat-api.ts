import type { RunAgentInput } from '@ag-ui/core'
import { expect, vi } from 'vitest'
import docxApproval from './docx-approval.json'

export const threadId = 'a904bd9b-33fa-4160-9caf-d34f9e52134c'
const firstPhysicalRunId = 'd7205d63-102b-4127-bcaf-665934361a10'
const resumedPhysicalRunId = '8b35686a-4212-4ccd-85ae-e5712ee52e01'
export const question = '합성 보고서 전송을 준비해줘'
export const answer = '승인이 반영되었습니다.'
// Public, model-free execute_in_skill capture from the scripted API. Native middleware
// supplies a tool-call ID on the stream and omits it from the interrupt.
export const docxInterruptId = docxApproval.interrupt.id

export const nativeQuestion = {
  question: '선택을 비워두면 기본 형식으로 진행합니다.',
  mode: 'option_list',
  options: [{ id: 'table', label: '표로 정리' }],
  minSelections: 0,
  maxSelections: 1,
}

export const nativeId = 'd620d4e91a63c684077ae8cbb246f7b9'
export const confirmName = '선택 확인 (0)'
// Matches the public response schema generated for the native ask_user interrupt.
const nativeResponseSchema = {
  $schema: 'https://json-schema.org/draft/2020-12/schema',
  type: 'object',
  required: ['decisions'],
  additionalProperties: false,
  properties: {
    decisions: {
      type: 'array',
      minItems: 1,
      maxItems: 1,
      items: false,
      prefixItems: [
        {
          type: 'object',
          additionalProperties: false,
          required: ['type', 'message'],
          properties: { type: { const: 'respond' }, message: { type: 'string' } },
        },
      ],
    },
  },
}

export function installChatApi({
  activeRunId = null,
  delayNativeCheckpoint = false,
}: { activeRunId?: string | null; delayNativeCheckpoint?: boolean } = {}) {
  let physicalRunId = firstPhysicalRunId

  const toolCallId = delayNativeCheckpoint ? 'ask-call' : docxApproval.toolCallId
  let expired = false

  const selectedTool = delayNativeCheckpoint
    ? { name: 'ask_user', args: nativeQuestion }
    : docxApproval.interrupt.metadata.moldy.action_requests[0]
  const slot = () =>
    delayNativeCheckpoint
      ? {
          id: pendingId,
          reason: 'moldy.ask_user',
          message: nativeQuestion.question,
          responseSchema: nativeResponseSchema,
          ...(expired ? { expiresAt: '2025-01-01T00:00:00Z' } : {}),
          metadata: {
            moldy: {
              type: 'ask_user',
              ...nativeQuestion,
              ...(expired ? { actionable: false } : {}),
            },
          },
        }
      : { ...docxApproval.interrupt, id: pendingId }
  const observedRunningCheckpoint = vi.fn()
  const observedInterruption = vi.fn()
  const appliedResponses: NonNullable<RunAgentInput['resume']>[number]['payload'][] = []

  let phase: 'empty' | 'running' | 'interrupted' | 'completed' = activeRunId ? 'running' : 'empty'
  let selectedRunId = activeRunId
  const streamReads: string[] = []
  let pendingId = delayNativeCheckpoint ? nativeId : docxInterruptId
  let userId = activeRunId ? 'previous-user-question' : ''
  const commands: RunAgentInput[] = []
  const writes: string[] = []
  const snapshotReads = vi.fn()
  const user = () => ({ id: userId, role: 'user', content: question })
  const requestingTool = {
    id: delayNativeCheckpoint ? 'assistant-calling' : docxApproval.parentMessageId,
    role: 'assistant',
    content: '',
    toolCalls: [
      {
        id: toolCallId,
        type: 'function',
        function: { name: selectedTool.name, arguments: JSON.stringify(selectedTool.args) },
      },
    ],
  }
  const doneMessage = { id: 'assistant-done', role: 'assistant', content: answer }

  function snapshot() {
    const pending = phase === 'interrupted'
    const active = phase === 'running'
    const messages =
      phase === 'empty' ? [] : active ? [user()] : [user(), pending ? requestingTool : doneMessage]
    const acceptedId = selectedRunId
    return {
      threadId,
      protocolVersion: '1.0',
      projectionVersion: '1',
      checkpointId: acceptedId ? 'checkpoint-A' : null,
      activeTipMessageId: messages.at(-1)?.id ?? null,
      runId: acceptedId,
      latestPhysicalRunId: acceptedId ? physicalRunId : null,
      runStatus: acceptedId ? phase : null,
      activeRunId: active ? selectedRunId : null,
      physicalRunId: active ? physicalRunId : null,
      interruptRunId: pending ? acceptedId : null,
      physicalInterruptRunId: pending ? physicalRunId : null,
      messages,
      interrupts: pending ? [slot()] : [],
      activities: [],
      subagents: [],
      cursor: null,
      messageLinks: messages.map((message, index) => ({
        messageId: message.id,
        uiMessageId:
          index === 0
            ? '60735f5d-d6e9-4cca-b050-46e6d47be6ea'
            : 'a308c6bd-d3ce-453b-914d-849ea287f196',
        parentMessageId: index === 0 ? null : userId,
        runId: acceptedId,
        physicalRunId,
        checkpointId: 'checkpoint-A',
        parentCheckpointId: null,
      })),
    }
  }

  function streamResponse(runId: string, interrupt: boolean) {
    const run = { threadId, runId: runId }
    const events = [
      { type: 'RUN_STARTED', ...run, protocolVersion: '1.0' },
      ...(interrupt
        ? [
            {
              type: 'TOOL_CALL_START',
              toolCallId,
              toolCallName: selectedTool.name,
              parentMessageId: requestingTool.id,
            },
            { type: 'TOOL_CALL_ARGS', toolCallId, delta: JSON.stringify(selectedTool.args) },
            { type: 'TOOL_CALL_END', toolCallId },
          ]
        : [
            { type: 'TEXT_MESSAGE_START', messageId: doneMessage.id, role: 'assistant' },
            { type: 'TEXT_MESSAGE_CONTENT', messageId: doneMessage.id, delta: answer },
            { type: 'TEXT_MESSAGE_END', messageId: doneMessage.id },
          ]),
      {
        type: 'RUN_FINISHED',
        ...run,
        outcome: interrupt ? { type: 'interrupt', interrupts: [slot()] } : { type: 'success' },
      },
    ]
    return new Response(events.map((event) => 'data: ' + JSON.stringify(event) + '\n\n').join(''), {
      headers: {
        'Content-Type': 'text/event-stream',
        'X-Run-Id': runId,
        'X-Moldy-Run-Id': physicalRunId,
      },
    })
  }

  async function fetchFixture(input: RequestInfo | URL, init?: RequestInit) {
    const path = new URL(input instanceof Request ? input.url : String(input), 'http://localhost')
      .pathname
    const method = init?.method ?? 'GET'
    if (method !== 'GET') writes.push(path)
    if (method === 'GET' && path.endsWith('/ag-ui/snapshot')) {
      snapshotReads()
      if (delayNativeCheckpoint && phase === 'running') observedRunningCheckpoint()
      if (delayNativeCheckpoint && phase === 'interrupted') observedInterruption()
      return Response.json(snapshot())
    }
    if (method === 'GET' && path.endsWith('/ag-ui/stream')) {
      const address = new URL(
        input instanceof Request ? input.url : String(input),
        'http://localhost',
      )
      const runId = address.searchParams.get('runId')
      if (!runId || runId !== selectedRunId) return new Response(null, { status: 404 })
      streamReads.push(runId)
      if (delayNativeCheckpoint && phase === 'running') return streamResponse(runId, true)
      phase = 'completed'
      return streamResponse(runId, false)
    }
    if (method === 'GET' && path.endsWith('/run-inputs'))
      return Response.json({ items: [], queue_paused: false })
    if (method === 'GET' && path.endsWith('/child-tasks'))
      return Response.json({
        items: [],
        next_offset: null,
        enabled: true,
        allow_legacy_details: false,
      })
    if (method === 'POST' && path.endsWith('/followup-suggestion'))
      return Response.json({ suggestion: null })
    if (method !== 'POST' || path !== '/api/conversations/' + threadId + '/ag-ui/runs') {
      return Response.json({ detail: 'Unconfigured test endpoint' }, { status: 404 })
    }
    expect(init?.credentials).toBe('include')
    expect(new Headers(init?.headers).get('X-CSRF-Token')).toBe('synthetic-csrf')
    const command: RunAgentInput = JSON.parse(String(init?.body))
    commands.push(command)
    const isFirst = commands.length === 1
    if (isFirst) userId = command.messages.at(-1)?.id ?? ''
    if (delayNativeCheckpoint && !isFirst) {
      if (phase !== 'interrupted' || command.resume?.[0]?.interruptId !== pendingId)
        return Response.json({ detail: 'Interrupt is not currently actionable' }, { status: 409 })
      appliedResponses.push(command.resume[0].payload)
    }
    selectedRunId = command.runId
    if (!isFirst) physicalRunId = resumedPhysicalRunId
    phase = isFirst ? (delayNativeCheckpoint ? 'running' : 'interrupted') : 'completed'
    return streamResponse(command.runId, isFirst)
  }
  vi.stubGlobal('fetch', vi.fn(fetchFixture))
  return {
    commands,
    streamReads,
    writes,
    observedRunningCheckpoint,
    appliedResponses,
    observedInterruption,
    commitInterrupt: (expiredAtCommit = false) => {
      expired = expiredAtCommit
      phase = 'interrupted'
    },
    snapshotReads,
    rotateApproval: () => {
      pendingId = 'current-approved-identity'
    },
  }
}
