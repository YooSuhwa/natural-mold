import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { AnyStream } from '@langchain/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { memoryKeys } from '@/lib/hooks/use-memory'
import type { MemoryEventPayload } from '@/lib/types'
import { toast } from 'sonner'
import { protocolMemoryEvent, useLangGraphMemoryEffects } from '../memory-events'

const mocks = vi.hoisted(() => ({
  useChannelEffect: vi.fn(),
}))

vi.mock('@langchain/react', () => ({
  useChannelEffect: mocks.useChannelEffect,
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

type ChannelEffectOptions = {
  readonly replay?: boolean
  readonly onEvent: (event: unknown) => void
}

function memoryPayload(overrides: Partial<MemoryEventPayload> = {}): MemoryEventPayload {
  return {
    id: 'memory-1',
    scope: 'user',
    content: 'User prefers concise answers.',
    reason: null,
    policy: 'auto',
    agent_id: 'agent-1',
    conversation_id: 'conversation-1',
    source_run_id: 'run-1',
    ...overrides,
  }
}

function protocolEvent(payload: MemoryEventPayload) {
  return {
    type: 'event',
    method: 'custom:memory_saved',
    event_id: 'event-memory-1',
    seq: 9,
    run_id: 'run-1',
    params: {
      namespace: [],
      data: payload,
    },
  }
}

describe('protocolMemoryEvent', () => {
  it('unwraps named custom memory payloads', () => {
    const payload = memoryPayload()

    expect(
      protocolMemoryEvent({
        method: 'custom',
        params: { data: { name: 'memory_saved', payload } },
      }),
    ).toEqual({ eventName: 'memory_saved', payload })
    expect(protocolMemoryEvent(protocolEvent(payload))).toEqual({ eventName: 'memory_saved', payload })
  })
})

describe('useLangGraphMemoryEffects', () => {
  beforeEach(() => {
    mocks.useChannelEffect.mockReset()
    vi.mocked(toast.info).mockReset()
    vi.mocked(toast.success).mockReset()
    vi.mocked(toast.warning).mockReset()
  })

  it('invalidates memory queries and shows one toast for live v3 memory custom events', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const stream = { kind: 'stream' } as unknown as AnyStream
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )

    renderHook(() => useLangGraphMemoryEffects({ stream }), { wrapper })

    const effectOptions = mocks.useChannelEffect.mock.calls[0]?.[2] as
      | ChannelEffectOptions
      | undefined
    expect(effectOptions).toEqual(expect.objectContaining({ replay: false }))

    act(() => {
      effectOptions?.onEvent(protocolEvent(memoryPayload()))
      effectOptions?.onEvent(protocolEvent(memoryPayload()))
    })

    expect(invalidateSpy).toHaveBeenCalledTimes(1)
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: memoryKeys.all })
    expect(toast.success).toHaveBeenCalledTimes(1)
    expect(toast.success).toHaveBeenCalledWith('savedToast')
  })
})
