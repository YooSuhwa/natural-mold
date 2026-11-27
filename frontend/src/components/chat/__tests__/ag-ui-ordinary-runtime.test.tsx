import { cleanup } from '@testing-library/react'
import { type RunAgentInput } from '@ag-ui/core'
import { afterEach, expect, it, vi } from 'vitest'
import { render, screen, userEvent, waitFor } from '../../../../tests/test-utils'
import { csrfStore } from '@/lib/auth/csrf'
import { AgUiRuntimeSection } from '../ag-ui-runtime-section'

const threadId = 'a904bd9b-33fa-4160-9caf-d34f9e52134c'
const physicalRunId = 'd7205d63-102b-4127-bcaf-665934361a10'
const question = '합성 보고서 전송을 준비해줘'
const answer = '승인이 반영되었습니다.'
const recipient = 'synthetic@example.invalid'
const toolCallId = 'mail-call'
const args = { to: recipient }

function approval(id: string) {
  return {
    id,
    reason: 'moldy.tool_approval',
    message: '보고서를 보내도 될까요?',
    responseSchema: { type: 'object' },
    metadata: {
      moldy: {
        action_requests: [{ name: 'send_mail', args }],
        review_configs: [{ action_name: 'send_mail', allowed_decisions: ['approve', 'reject'] }],
      },
    },
  }
}

function installChatApi() {
  let pendingId = 'original-approval'
  let userId = ''
  const commands: RunAgentInput[] = []
  const writes: string[] = []
  const snapshotReads = vi.fn()
  const user = () => ({ id: userId, role: 'user', content: question })
  const requestingTool = {
    id: 'assistant-calling',
    role: 'assistant',
    content: '',
    toolCalls: [
      {
        id: toolCallId,
        type: 'function',
        function: { name: 'send_mail', arguments: JSON.stringify(args) },
      },
    ],
  }
  const doneMessage = { id: 'assistant-done', role: 'assistant', content: answer }

  function snapshot() {
    const pending = commands.length === 1
    const messages =
      commands.length === 0 ? [] : pending ? [user(), requestingTool] : [user(), doneMessage]
    const acceptedId = commands.at(-1)?.runId ?? null
    return {
      threadId,
      protocolVersion: '1.0',
      projectionVersion: '1',
      checkpointId: acceptedId ? 'checkpoint-A' : null,
      activeTipMessageId: messages.at(-1)?.id ?? null,
      runId: acceptedId,
      latestPhysicalRunId: acceptedId ? physicalRunId : null,
      runStatus: acceptedId ? (pending ? 'interrupted' : 'completed') : null,
      activeRunId: null,
      physicalRunId: null,
      interruptRunId: pending ? acceptedId : null,
      physicalInterruptRunId: pending ? physicalRunId : null,
      messages,
      interrupts: pending ? [approval(pendingId)] : [],
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

  async function fetchFixture(input: RequestInfo | URL, init?: RequestInit) {
    const path = new URL(input instanceof Request ? input.url : String(input), 'http://localhost')
      .pathname
    const method = init?.method ?? 'GET'
    if (method !== 'GET') writes.push(path)
    if (method === 'GET' && path.endsWith('/ag-ui/snapshot')) {
      snapshotReads()
      return Response.json(snapshot())
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
    const run = { threadId, runId: command.runId }
    const events = [
      { type: 'RUN_STARTED', ...run, protocolVersion: '1.0' },
      ...(isFirst
        ? [
            {
              type: 'TOOL_CALL_START',
              toolCallId,
              toolCallName: 'send_mail',
              parentMessageId: 'assistant-calling',
            },
            { type: 'TOOL_CALL_ARGS', toolCallId, delta: JSON.stringify(args) },
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
        outcome: isFirst
          ? { type: 'interrupt', interrupts: [approval(pendingId)] }
          : { type: 'success' },
      },
    ]
    return new Response(events.map((event) => 'data: ' + JSON.stringify(event) + '\n\n').join(''), {
      headers: {
        'Content-Type': 'text/event-stream',
        'X-Run-Id': command.runId,
        'X-Moldy-Run-Id': physicalRunId,
      },
    })
  }
  vi.stubGlobal('fetch', vi.fn(fetchFixture))
  return {
    commands,
    writes,
    snapshotReads,
    rotateApproval: () => {
      pendingId = 'current-approved-identity'
    },
  }
}

function openChat() {
  return render(
    <AgUiRuntimeSection
      agentId="synthetic-agent"
      conversationId={threadId}
      serverMessages={[]}
      serverLatestRun={null}
      onRuntimeStatusChange={vi.fn()}
      onStreamEnd={vi.fn()}
      threadProps={{ conversationId: threadId, emptyContent: '대화를 시작하세요' }}
    />,
  )
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  csrfStore.clear()
})

it('sends once, reopens the pending approval by GET, then resumes the current interrupt from the chat card', async () => {
  csrfStore.set('synthetic-csrf')
  const api = installChatApi()
  const user = userEvent.setup()
  const first = openChat()
  await waitFor(() => expect(api.snapshotReads).toHaveBeenCalled())
  await user.type(await screen.findByRole('textbox'), question + '{Enter}')
  expect(await screen.findByRole('button', { name: '승인' })).toBeVisible()
  expect(api.commands).toHaveLength(1)
  expect(api.commands[0].messages).toMatchObject([{ role: 'user', content: question }])
  first.unmount()

  api.rotateApproval()
  const beforeReload = api.snapshotReads.mock.calls.length
  openChat()
  const approve = await screen.findByRole('button', { name: '승인' })
  expect(api.snapshotReads.mock.calls.length).toBeGreaterThan(beforeReload)
  expect(api.writes).toHaveLength(1)
  await user.click(approve)
  expect(await screen.findByText(answer)).toBeVisible()
  const chatRunPath = '/api/conversations/' + threadId + '/ag-ui/runs'
  await waitFor(() =>
    expect(api.writes).toEqual([
      chatRunPath,
      chatRunPath,
      '/api/conversations/' + threadId + '/followup-suggestion',
    ]),
  )
  expect(api.commands[1]).toMatchObject({
    threadId,
    messages: [],
    resume: [
      {
        interruptId: 'current-approved-identity',
        status: 'resolved',
        payload: { decisions: [{ type: 'approve' }] },
      },
    ],
  })
  expect(api.commands[1].runId).not.toBe(api.commands[0].runId)
})
