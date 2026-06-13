import { act, renderHook } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AIMessage } from '@langchain/core/messages'
import type { AnyStream } from '@langchain/react'
import { Provider, createStore } from 'jotai'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { artifactKeys } from '@/lib/api/artifacts'
import { chatArtifactsAtom } from '@/lib/stores/chat-artifacts'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import type { FileEventPayload } from '@/lib/types'
import {
  protocolArtifactPayload,
  useLangGraphArtifactEffects,
} from '../artifact-events'

const mocks = vi.hoisted(() => ({
  useChannelEffect: vi.fn(),
}))

vi.mock('@langchain/react', () => ({
  useChannelEffect: mocks.useChannelEffect,
}))

type ChannelEffectOptions = {
  replay?: boolean
  onEvent: (event: unknown) => void
}

function artifact(overrides: Partial<FileEventPayload> = {}): FileEventPayload {
  return {
    op: 'created',
    id: 'artifact-1',
    agent_id: 'agent-1',
    conversation_id: 'conversation-1',
    assistant_msg_id: 'assistant-1',
    run_id: 'run-1',
    tool_call_id: 'call-1',
    source_tool_name: 'execute_in_skill',
    path: 'report.md',
    display_name: 'report.md',
    mime_type: 'text/markdown',
    extension: 'md',
    artifact_kind: 'markdown',
    size_bytes: 12,
    sha256: 'a'.repeat(64),
    status: 'ready',
    is_favorite: false,
    last_opened_at: null,
    preview_count: 0,
    download_count: 0,
    version_id: 'version-1',
    version_number: 1,
    created_at: '2026-06-05T00:00:00',
    updated_at: '2026-06-05T00:00:00',
    agent_name: null,
    conversation_title: null,
    url: '/api/conversations/conversation-1/artifacts/artifact-1',
    preview_url: '/api/conversations/conversation-1/artifacts/artifact-1/content',
    download_url: '/api/conversations/conversation-1/artifacts/artifact-1/download',
    ...overrides,
  }
}

function protocolEvent(payload: FileEventPayload) {
  return {
    type: 'event',
    method: 'custom:file_event',
    event_id: 'event-file-1',
    seq: 7,
    run_id: 'run-1',
    params: {
      namespace: [],
      data: payload,
    },
  }
}

function artifactSummary(payload: FileEventPayload) {
  const { op: _op, ...summary } = payload
  void _op
  return summary
}

describe('protocolArtifactPayload', () => {
  it('unwraps named custom artifact payloads', () => {
    const payload = artifact()

    expect(
      protocolArtifactPayload({
        method: 'custom',
        params: { data: { name: 'file_event', payload } },
      }),
    ).toEqual(payload)
    expect(protocolArtifactPayload(protocolEvent(payload))).toEqual(payload)
  })
})

describe('useLangGraphArtifactEffects', () => {
  beforeEach(() => {
    mocks.useChannelEffect.mockReset()
  })

  it('applies v3 artifact custom events to stores, right rail, queries, and live messages', () => {
    const store = createStore()
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const stream = { kind: 'stream' } as unknown as AnyStream
    const assistantMessage = new AIMessage({ id: 'assistant-1', content: '초안입니다.' })
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <Provider store={store}>{children}</Provider>
      </QueryClientProvider>
    )

    const { result } = renderHook(
      () =>
        useLangGraphArtifactEffects({
          stream,
          conversationId: 'conversation-1',
          messages: [assistantMessage],
        }),
      { wrapper },
    )

    const effectOptions = mocks.useChannelEffect.mock.calls[0]?.[2] as
      | ChannelEffectOptions
      | undefined
    expect(effectOptions).toEqual(expect.objectContaining({ replay: true }))

    act(() => {
      effectOptions?.onEvent(protocolEvent(artifact()))
    })

    const conversationArtifacts = store.get(chatArtifactsAtom)['conversation-1']
    expect(conversationArtifacts?.items.map((item) => item.id)).toEqual(['artifact-1'])
    expect(conversationArtifacts?.selectedArtifactId).toBe('artifact-1')
    expect(store.get(chatRightRailAtom)).toEqual({
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        selectedArtifactId: 'artifact-1',
        view: 'preview',
      },
    })
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: artifactKeys.all })
    expect((result.current[0] as { artifacts?: unknown[] }).artifacts).toEqual([
      artifactSummary(artifact()),
    ])
  })
})
