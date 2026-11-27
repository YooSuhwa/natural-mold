import { cleanup } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { render, screen, userEvent, waitFor } from '../../../../tests/test-utils'
import {
  installChatApi,
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
