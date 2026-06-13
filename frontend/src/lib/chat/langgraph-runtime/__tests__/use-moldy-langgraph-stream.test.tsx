import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useMoldyLangGraphStream } from '../use-moldy-langgraph-stream'
import type { AttachmentAdapter, CompleteAttachment, PendingAttachment } from '@assistant-ui/react'

const mocks = vi.hoisted(() => {
  const stream = {
    messages: [],
    values: { messages: [] },
    isLoading: false,
    submit: vi.fn(),
    stop: vi.fn(),
  }
  return {
    stream,
    createMoldyAgentTransport: vi.fn((conversationId: string) => ({
      kind: 'transport',
      conversationId,
    })),
    useStream: vi.fn(() => stream),
    useChannel: vi.fn(() => []),
    useExternalMessageConverter: vi.fn(() => [{ id: 'converted' }]),
    useExternalStoreRuntime: vi.fn((options: unknown) => ({ kind: 'runtime', options })),
    convertLangChainBaseMessage: vi.fn(),
  }
})

vi.mock('../moldy-agent-transport', () => ({
  createMoldyAgentTransport: mocks.createMoldyAgentTransport,
}))

vi.mock('@langchain/react', () => ({
  useChannel: mocks.useChannel,
  useStream: mocks.useStream,
}))

vi.mock('@assistant-ui/react', () => ({
  useExternalMessageConverter: mocks.useExternalMessageConverter,
  useExternalStoreRuntime: mocks.useExternalStoreRuntime,
}))

vi.mock('@assistant-ui/react-langchain', () => ({
  convertLangChainBaseMessage: mocks.convertLangChainBaseMessage,
}))

describe('useMoldyLangGraphStream', () => {
  it('creates one LangChain stream and bridges it into assistant-ui', () => {
    const feedbackAdapter = { submit: vi.fn() }
    const attachmentAdapter: AttachmentAdapter = {
      accept: 'image/*',
      add: vi.fn(
        async (state: { file: File }): Promise<PendingAttachment> => ({
          id: 'pending-attachment',
          type: 'file',
          name: state.file.name,
          contentType: state.file.type,
          file: state.file,
          status: { type: 'requires-action', reason: 'composer-send' },
        }),
      ),
      send: vi.fn(
        async (attachment): Promise<CompleteAttachment> => ({
          ...attachment,
          status: { type: 'complete' },
        }),
      ),
      remove: vi.fn(async () => {}),
    }

    const { result } = renderHook(() =>
      useMoldyLangGraphStream({
        agentId: 'agent-1',
        conversationId: 'conversation-1',
        feedbackAdapter,
        attachmentAdapter,
      }),
    )

    expect(mocks.createMoldyAgentTransport).toHaveBeenCalledWith('conversation-1', 'agent-1')
    expect(mocks.useStream).toHaveBeenCalledWith({
      transport: { kind: 'transport', conversationId: 'conversation-1' },
      threadId: 'conversation-1',
    })
    expect(mocks.useExternalStoreRuntime).toHaveBeenCalledWith(
      expect.objectContaining({
        messages: [{ id: 'converted' }],
        isRunning: false,
        adapters: {
          feedback: feedbackAdapter,
          attachments: attachmentAdapter,
        },
      }),
    )
    expect(result.current.stream).toBe(mocks.stream)
    expect(result.current.activities).toEqual([])
    expect(result.current.deepAgentsState).toEqual({ todos: [], files: [] })
    expect(result.current.assistantRuntime).toEqual(expect.objectContaining({ kind: 'runtime' }))
  })

  it('submits new assistant-ui messages through the same LangChain stream', async () => {
    renderHook(() =>
      useMoldyLangGraphStream({
        agentId: 'agent-2',
        conversationId: 'conversation-2',
      }),
    )

    const runtimeOptions = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0] as {
      onNew: (message: { content: { type: string; text: string }[] }) => Promise<void>
      onCancel: () => Promise<void>
    }
    await runtimeOptions.onNew({ content: [{ type: 'text', text: 'hello' }] })
    await runtimeOptions.onCancel()

    expect(mocks.stream.submit).toHaveBeenCalledWith({
      messages: [expect.objectContaining({ content: 'hello' })],
    })
    expect(mocks.stream.stop).toHaveBeenCalled()
  })
})
