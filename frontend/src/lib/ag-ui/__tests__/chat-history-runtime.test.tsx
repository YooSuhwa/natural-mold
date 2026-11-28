import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useAgUiRuntime } from '@assistant-ui/react-ag-ui'
import type { ThreadMessage } from '@assistant-ui/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { useChatSession } from '../use-chat-session'

const threadId = '92a41f8c-8129-4676-9af5-f29a82424bc8'
const physicalRunId = '87a669a9-e91e-4729-8c80-cd4857a70400'
const publicRunId = 'run-before-refresh'
const conversationPath = `/api/conversations/${threadId}/ag-ui`
const userMessageId = '43216492-ba70-492a-8641-5faf6dabf076'
const assistantMessageId = '669e2b59-9c7b-4aa3-99e3-dda56fa9e2b9'
const checkpointId = 'checkpoint-before-refresh'
const currentInterruptId = 'approval-after-rotation'
const answerText = 'The replayed answer is complete.'

type Mode = 'completed' | 'running' | 'interrupted'

function historySnapshot(mode: Mode) {
  const assistantText = {
    running: 'The replayed',
    interrupted: 'Awaiting a choice.',
    completed: 'Finished earlier.',
  }[mode]
  const messages = [
    { id: 'wire-user', role: 'user', content: 'Summarize these notes.' },
    { id: 'wire-answer', role: 'assistant', content: assistantText },
  ]
  const messageLinks = messages.map(({ id, role }) => ({
    messageId: id,
    uiMessageId: role === 'user' ? userMessageId : assistantMessageId,
    runId: publicRunId,
    physicalRunId,
    checkpointId,
    parentCheckpointId: role === 'user' ? null : checkpointId,
    parentMessageId: role === 'user' ? null : 'wire-user',
  }))
  return {
    threadId,
    protocolVersion: '1.0',
    projectionVersion: '1',
    checkpointId,
    activeTipMessageId: 'wire-answer',
    activeRunId: mode === 'running' ? publicRunId : null,
    physicalRunId: mode === 'running' ? physicalRunId : null,
    interruptRunId: mode === 'interrupted' ? publicRunId : null,
    physicalInterruptRunId: mode === 'interrupted' ? physicalRunId : null,
    messages,
    messageLinks,
    subagents: [],
    cursor: null,
    interrupts:
      mode === 'interrupted'
        ? [
            {
              id: currentInterruptId,
              reason: 'moldy.ask_user',
              message: 'Use this outline?',
              responseSchema: { type: 'object' },
              metadata: { moldy: { type: 'ask_user', question: 'Use this outline?' } },
            },
          ]
        : [],
  }
}

function streamResponse(runId: string, physicalId: string) {
  const messageId = runId === publicRunId ? 'wire-answer' : 'wire-after-approval'
  const frames = [
    { type: 'RUN_STARTED', threadId, runId, protocolVersion: '1.0' },
    { type: 'TEXT_MESSAGE_START', messageId, role: 'assistant' },
    { type: 'TEXT_MESSAGE_CONTENT', messageId, delta: answerText },
    { type: 'TEXT_MESSAGE_END', messageId },
    { type: 'RUN_FINISHED', threadId, runId, outcome: { type: 'success' } },
  ]
  return new Response(frames.map((frame) => 'data: ' + JSON.stringify(frame) + '\n\n').join(''), {
    headers: {
      'content-type': 'text/event-stream',
      'X-Moldy-Run-Id': physicalId,
      'X-Run-Id': runId,
    },
  })
}

function textOf(message: ThreadMessage) {
  return message.content.flatMap((part) => (part.type === 'text' ? [part.text] : [])).join('')
}

function openHistory(mode: Mode) {
  const snapshot = historySnapshot(mode)
  const requests: { method: string; url: URL; init?: RequestInit }[] = []
  const fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    const address = new URL(url instanceof Request ? url.url : String(url), 'http://localhost')
    const method = init?.method ?? 'GET'
    requests.push({ method, url: address, init })
    if (method === 'GET' && address.pathname === conversationPath + '/snapshot') {
      return Response.json(snapshot)
    }
    if (method === 'GET' && address.pathname === conversationPath + '/stream') {
      return streamResponse(publicRunId, physicalRunId)
    }
    if (method === 'POST' && address.pathname === conversationPath + '/runs') {
      const command = JSON.parse(String(init?.body)) as { runId: string }
      return streamResponse(command.runId, '006f37f5-b064-4f10-a165-ec0b1836e76e')
    }
    throw new Error('Unexpected request: ' + method + ' ' + address.pathname)
  })
  vi.stubGlobal('fetch', fetch)
  const onError = vi.fn()
  const hook = renderHook(() => {
    const { agent, session } = useChatSession({ conversationId: threadId, serverMessages: [] })
    const runtime = useAgUiRuntime({
      agent,
      adapters: { history: session.history },
      resumeTranscript: 'appended',
      onError,
    })
    return { runtime, session }
  })
  return { ...hook, requests, onError }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('ordinary chat history on the official AG-UI runtime', () => {
  it('opens a completed conversation with its protected message identity and no model request', async () => {
    const { result, requests, onError } = openHistory('completed')
    await waitFor(() => {
      expect(result.current.runtime.thread.getState().messages.map(textOf)).toEqual([
        'Summarize these notes.',
        'Finished earlier.',
      ])
    })
    expect(
      result.current.runtime.thread.getState().messages[1]?.metadata.custom.publicMessageId,
    ).toBe(assistantMessageId)
    expect(requests.map(({ method, url }) => [method, url.pathname.split('/').at(-1)])).toEqual([
      ['GET', 'snapshot'],
    ])
    expect(onError).not.toHaveBeenCalled()
  })

  it('reopens the existing execution by GET and displays its replayed answer once', async () => {
    const { result, requests, onError } = openHistory('running')
    await waitFor(() => expect(result.current.session.getSnapshot().activeRunId).toBe(publicRunId))
    await waitFor(() => {
      expect(result.current.runtime.thread.getState().isLoading).toBe(false)
      expect(result.current.runtime.thread.getState().messages.map(textOf)).toEqual([
        'Summarize these notes.',
      ])
    })
    expect(requests).toHaveLength(1)
    const headId = result.current.runtime.thread.getState().messages.at(-1)?.id ?? null
    await act(async () => {
      result.current.runtime.thread.resumeRun({ parentId: headId })
    })
    await waitFor(() => {
      expect(result.current.runtime.thread.getState().messages.map(textOf)).toEqual([
        'Summarize these notes.',
        answerText,
      ])
      expect(result.current.runtime.thread.getState().isRunning).toBe(false)
    })
    expect(requests.map(({ method, url }) => [method, url.pathname.split('/').at(-1)])).toEqual([
      ['GET', 'snapshot'],
      ['GET', 'stream'],
    ])
    expect(requests[1]?.url.searchParams.get('runId')).toBe(publicRunId)
    expect(onError).not.toHaveBeenCalled()
  })

  it('restores the paused checkpoint and submits only the current interrupt when answered', async () => {
    const { result, requests, onError } = openHistory('interrupted')
    await waitFor(() =>
      expect(
        result.current.runtime.unstable_getPendingInterrupts().map((interrupt) => interrupt.id),
      ).toEqual([currentInterruptId]),
    )
    expect(result.current.runtime.thread.getState().messages.at(-1)).toMatchObject({
      status: { type: 'requires-action', reason: 'interrupt' },
    })
    expect(requests.map(({ method, url }) => [method, url.pathname.split('/').at(-1)])).toEqual([
      ['GET', 'snapshot'],
    ])
    const response = {
      interruptId: currentInterruptId,
      status: 'resolved' as const,
      payload: { decisions: [{ type: 'respond', message: 'Yes' }] },
    }
    await expect(
      result.current.runtime.unstable_submitInterruptResponses([
        { ...response, interruptId: 'approval-before-rotation' },
      ]),
    ).rejects.toThrow()
    expect(requests.filter(({ method }) => method === 'POST')).toHaveLength(0)
    await act(async () => {
      await result.current.runtime.unstable_submitInterruptResponses([response])
    })
    await waitFor(() => expect(result.current.runtime.unstable_getPendingInterrupts()).toEqual([]))
    const posts = requests.filter(({ method }) => method === 'POST')
    expect(posts).toHaveLength(1)
    const command = JSON.parse(String(posts[0]?.init?.body)) as { runId: string }
    expect(command).toMatchObject({ threadId, messages: [], resume: [response] })
    expect(command.runId).not.toBe(publicRunId)
    expect(result.current.runtime.thread.getState().messages.map(textOf)).toContain(answerText)
    expect(onError).not.toHaveBeenCalled()
  })
})
