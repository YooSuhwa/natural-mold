import * as navigation from 'next/navigation'
import { cleanup } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { render, screen, userEvent, waitFor } from '../../../../tests/test-utils'
import {
  installChatApi,
  docxInterruptId,
  docxIssuerId,
  threadId,
  question,
  answer,
  nativeId,
  nativeQuestion,
  confirmName,
} from '../../../../tests/ag-ui/ordinary-chat-api'
import { csrfStore } from '@/lib/auth/csrf'
import { AgUiRuntimeSection } from '../ag-ui-runtime-section'

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

async function expectIssuerDecision(label: '거부됨' | '승인됨') {
  const badge = await screen.findByText(label)
  expect(badge).toBeVisible()
  expect(badge.closest('[data-moldy-message-role="assistant"]')).toHaveAttribute(
    'data-moldy-wire-message-id',
    docxIssuerId,
  )
  expect(screen.queryByRole('button', { name: '거부' })).not.toBeInTheDocument()
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
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
  await waitFor(() => expect(screen.getByText(answer)).toBeVisible())
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

it('replays another tab’s running answer without submitting a run or generating a new suggestion', async () => {
  const api = installChatApi({ activeRunId: 'original-active-run' })
  openChat()
  await waitFor(() => {
    expect(api.snapshotReads.mock.calls.length).toBeGreaterThan(1)
    expect(screen.getByText(question)).toBeVisible()
    expect(screen.getByText(answer)).toBeVisible()
  })
  expect(api.streamReads).toEqual(['original-active-run'])
  expect(api.commands).toHaveLength(0)
  expect(api.writes).toEqual([])
})

it('offers a native answer only after its checkpoint commits and applies the first click once', async () => {
  csrfStore.set('synthetic-csrf')
  const api = installChatApi({ delayNativeCheckpoint: true })
  const user = userEvent.setup()
  openChat()
  await waitFor(() => expect(api.snapshotReads).toHaveBeenCalled())
  await user.type(await screen.findByRole('textbox'), question + '{Enter}')

  // The stream has already published RUN_FINISHED.interrupt, but the selected
  // database read still identifies the parent run as running with no pending slots.
  await waitFor(() => expect(api.observedRunningCheckpoint).toHaveBeenCalled())
  const premature = screen.queryByRole('button', { name: confirmName })
  if (premature) expect(premature).toBeDisabled()
  expect(api.writes).toEqual(['/api/conversations/' + threadId + '/ag-ui/runs'])
  expect(api.appliedResponses).toEqual([])

  api.commitInterrupt()
  await waitFor(() => expect(screen.getByRole('button', { name: confirmName })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: confirmName }))

  const expected = {
    decisions: [
      {
        type: 'respond',
        message: JSON.stringify({ mode: 'option_list', selection: [], labels: [] }),
      },
    ],
  }
  await waitFor(() => expect(screen.getByText(answer)).toBeVisible())
  expect(api.commands).toHaveLength(2)
  expect(api.commands[0].messages).toMatchObject([{ role: 'user', content: question }])
  expect(api.commands[1]).toMatchObject({
    threadId,
    messages: [],
    resume: [{ interruptId: nativeId, status: 'resolved', payload: expected }],
  })
  expect(api.appliedResponses).toEqual([expected])
  const runPath = '/api/conversations/' + threadId + '/ag-ui/runs'
  await waitFor(() =>
    expect(api.writes).toEqual([
      runPath,
      runPath,
      '/api/conversations/' + threadId + '/followup-suggestion',
    ]),
  )
})

it('keeps an expired native question read-only when reopening its committed checkpoint', async () => {
  csrfStore.set('synthetic-csrf')
  const api = installChatApi({ delayNativeCheckpoint: true })
  const origin = openChat()
  await waitFor(() => expect(api.snapshotReads).toHaveBeenCalled())
  await userEvent.setup().type(await screen.findByRole('textbox'), question + '{Enter}')
  await waitFor(() => expect(api.observedRunningCheckpoint).toHaveBeenCalled())
  origin.unmount()
  api.commitInterrupt(true)
  openChat()
  await waitFor(() => expect(api.observedInterruption).toHaveBeenCalled())
  expect(await screen.findByText(nativeQuestion.question)).toBeVisible()
  const expiredControl = screen.queryByRole('button', { name: confirmName })
  if (expiredControl) expect(expiredControl).toBeDisabled()
  expect(api.appliedResponses).toEqual([])
  expect(api.writes).toEqual(['/api/conversations/' + threadId + '/ag-ui/runs'])
})

it('rejects the captured execute_in_skill request only after confirmation', async () => {
  csrfStore.set('synthetic-csrf')
  const api = installChatApi()
  const user = userEvent.setup()
  const original = openChat()
  await user.type(await screen.findByRole('textbox'), question + '{Enter}')
  expect(await screen.findByText(/승인이 필요합니다/)).toBeVisible()
  const reason = '합성 문서를 생성할 필요가 없습니다.'
  await user.click(await screen.findByRole('button', { name: '거부' }))
  await user.type(screen.getByRole('textbox', { name: '거부 사유' }), reason)
  expect(api.commands).toHaveLength(1)
  await user.click(screen.getByRole('button', { name: '거부 확인' }))
  await waitFor(() => expect(api.commands).toHaveLength(2))
  expect(api.commands[1].resume).toEqual([
    {
      interruptId: docxInterruptId,
      status: 'resolved',
      payload: { decisions: [{ type: 'reject', message: reason }] },
    },
  ])
  await waitFor(() => expect(api.observedCompletedSnapshot).toHaveBeenCalled())
  expect(await screen.findByText(answer)).toBeVisible()
  await expectIssuerDecision('거부됨')
  original.unmount()
  const before = api.snapshotReads.mock.calls.length
  const reopened = openChat()
  await waitFor(() => expect(api.snapshotReads.mock.calls.length).toBeGreaterThan(before))
  await expectIssuerDecision('거부됨')
  reopened.unmount()

  // The tool's error text remains identical. A subsequent authorized read may
  // withhold its approval record, in which case the decision must disappear.
  api.withholdDecisionEvidence()
  openChat()
  expect(await screen.findByText(answer)).toBeVisible()
  expect(screen.queryByText('거부됨')).not.toBeInTheDocument()
  expect(api.commands).toHaveLength(2)
})

it('grants later calls only on explicit request and approves the current tool call separately', async () => {
  csrfStore.set('synthetic-csrf')
  vi.spyOn(navigation, 'useParams').mockReturnValue({ agentId: 'synthetic-agent' })
  const api = installChatApi()
  const normalFetch = globalThis.fetch
  const tool = 'builtin:execute_in_skill'
  const grants: { id: string; tool_name: string; created_at: string }[] = []
  const requests: unknown[] = []
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(input instanceof Request ? input.url : String(input), 'http://localhost')
      .pathname
    if (path === '/api/agents/synthetic-agent/tool-approval' && (init?.method ?? 'GET') === 'GET')
      return Response.json({
        settings: null,
        company_actions: {},
        company_always_ask: [],
        tools: [
          {
            key: tool,
            name: 'execute_in_skill',
            grantable: true,
            locked: false,
            cells: [],
            level: 'code_execution',
          },
        ],
      })
    if (path === '/api/conversations/' + threadId + '/tool-grants') {
      if ((init?.method ?? 'GET') === 'GET') return Response.json(grants)
      if (init?.method === 'POST') {
        const body: unknown = JSON.parse(String(init.body))
        requests.push(body)
        expect(body).toEqual({ tool })
        const grant = {
          id: 'ca703cb1-3781-4256-af8a-39a4b994bb6c',
          tool_name: tool,
          created_at: '2026-11-27T03:00:00Z',
        }
        grants.push(grant)
        return Response.json(grant, { status: 201 })
      }
    }
    return normalFetch(input, init)
  })
  const user = userEvent.setup()
  openChat()
  await user.type(await screen.findByRole('textbox'), question + '{Enter}')
  const grant = await screen.findByRole('button', { name: '이 대화 동안 이 도구는 묻지 않기' })
  expect(api.commands).toHaveLength(1)
  expect(requests).toHaveLength(0)
  await user.click(grant)
  expect(await screen.findByRole('region', { name: '이 대화에서 허용한 도구' })).toHaveTextContent(
    'execute_in_skill',
  )
  expect(requests).toEqual([{ tool }])
  expect(api.commands).toHaveLength(1)
  await user.click(screen.getByRole('button', { name: '승인' }))
  await waitFor(() => expect(api.commands).toHaveLength(2))
  expect(api.commands[1].resume).toEqual([
    {
      interruptId: docxInterruptId,
      status: 'resolved',
      payload: { decisions: [{ type: 'approve' }] },
    },
  ])
  await waitFor(() => expect(api.observedCompletedSnapshot).toHaveBeenCalled())
  await expectIssuerDecision('승인됨')
})

it('preserves a draft until the selected history permits an explicit send', async () => {
  csrfStore.set('synthetic-csrf')
  const api = installChatApi()
  const original = globalThis.fetch
  const release = Promise.withResolvers<void>()
  const begun = vi.fn()
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(input instanceof Request ? input.url : String(input), 'http://localhost')
    if ((init?.method ?? 'GET') === 'GET' && url.pathname.endsWith('/ag-ui/snapshot')) {
      begun()
      await release.promise
    }
    return original(input, init)
  })
  try {
    openChat()
    await waitFor(() => expect(begun).toHaveBeenCalled())
    const user = userEvent.setup()
    const textarea = await screen.findByRole('textbox')
    expect(textarea).toBeEnabled()
    await user.type(textarea, question + '{Enter}')
    expect(textarea).toHaveValue(question)
    const send = screen.getByRole('button', { name: '전송' })
    expect(send).toBeDisabled()
    expect(api.commands).toHaveLength(0)
    release.resolve()
    await waitFor(() => expect(send).toBeEnabled())
    expect(textarea).toHaveValue(question)
    expect(api.commands).toHaveLength(0)
    await user.click(textarea)
    await user.keyboard('{Enter}')
    await waitFor(() => expect(api.commands).toHaveLength(1))
    expect(api.commands[0].messages).toMatchObject([{ role: 'user', content: question }])
  } finally {
    release.resolve()
  }
})
