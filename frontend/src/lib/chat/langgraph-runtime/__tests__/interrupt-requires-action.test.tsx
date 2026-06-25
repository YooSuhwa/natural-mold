import { renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { BaseMessage } from '@langchain/core/messages'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useMoldyLangGraphStream } from '../use-moldy-langgraph-stream'

type RuntimeOptions = {
  readonly messages: readonly unknown[]
  readonly isRunning: boolean
}

type ConverterOptions = {
  readonly messages: readonly BaseMessage[]
  readonly callback: (message: BaseMessage, metadata?: unknown) => unknown
  readonly isRunning: boolean
}

const mocks = vi.hoisted(() => {
  const STREAM_CONTROLLER = Symbol('STREAM_CONTROLLER')
  const metadataSnapshot = new Map()
  const metadataStore = {
    subscribe: vi.fn(() => () => {}),
    getSnapshot: vi.fn(() => metadataSnapshot),
  }
  const stream = {
    messages: [],
    values: { messages: [] },
    interrupts: [
      {
        id: 'interrupt-approval-1',
        value: {
          action_requests: [{ name: 'send_email', args: { to: 'team@example.com' } }],
          review_configs: [{ action_name: 'send_email', allowed_decisions: ['approve', 'reject'] }],
        },
      },
    ],
    isLoading: true,
    submit: vi.fn(),
    respond: vi.fn(),
    stop: vi.fn(),
    [STREAM_CONTROLLER]: { messageMetadataStore: metadataStore },
  }
  return {
    STREAM_CONTROLLER,
    stream,
    createMoldyAgentTransport: vi.fn(() => ({
      kind: 'transport',
      setRunStartAcceptedListener: vi.fn(),
    })),
    useStream: vi.fn(() => stream),
    useChannel: vi.fn(() => []),
    useChannelEffect: vi.fn(),
    useExternalStoreRuntime: vi.fn((options: RuntimeOptions) => ({ kind: 'runtime', options })),
  }
})

vi.mock('../moldy-agent-transport', () => ({
  createMoldyAgentTransport: mocks.createMoldyAgentTransport,
}))

vi.mock('@langchain/react', () => ({
  STREAM_CONTROLLER: mocks.STREAM_CONTROLLER,
  useChannel: mocks.useChannel,
  useChannelEffect: mocks.useChannelEffect,
  useStream: mocks.useStream,
}))

vi.mock('@assistant-ui/react', () => ({
  useExternalMessageConverter: vi.fn((options: ConverterOptions) =>
    options.messages.map((message) => options.callback(message)),
  ),
  useExternalStoreRuntime: mocks.useExternalStoreRuntime,
}))

vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

vi.mock('sonner', () => ({
  toast: {
    info: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
}))

function createQueryWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return function QueryWrapper({ children }: { readonly children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

function latestRuntimeOptions(): RuntimeOptions {
  const options = mocks.useExternalStoreRuntime.mock.calls.at(-1)?.[0]
  if (!options) throw new Error('runtime options were not captured')
  return options
}

describe('useMoldyLangGraphStream interrupt status', () => {
  beforeEach(() => {
    mocks.useExternalStoreRuntime.mockClear()
  })

  it('exposes loading interrupted LangGraph messages as assistant-ui requires-action', async () => {
    const assistantUi = await import('@assistant-ui/react')
    const useExternalMessageConverter = vi.mocked(assistantUi.useExternalMessageConverter)

    renderHook(
      () =>
        useMoldyLangGraphStream({
          agentId: 'agent-hitl',
          conversationId: 'conversation-hitl',
        }),
      { wrapper: createQueryWrapper() },
    )

    expect(useExternalMessageConverter).toHaveBeenLastCalledWith(
      expect.objectContaining({ isRunning: false }),
    )
    const runtimeOptions = latestRuntimeOptions()
    expect(runtimeOptions.isRunning).toBe(false)
    const runtimeMessages = runtimeOptions.messages
    expect(runtimeMessages).toEqual([
      expect.objectContaining({
        role: 'assistant',
        status: { type: 'requires-action', reason: 'tool-calls' },
        content: expect.arrayContaining([
          expect.objectContaining({
            type: 'tool-call',
            toolCallId: 'interrupt-approval-1:0',
            toolName: 'request_approval',
          }),
        ]),
      }),
    ])
  })
})
