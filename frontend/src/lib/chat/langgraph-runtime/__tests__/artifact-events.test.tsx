import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AIMessage, type BaseMessage } from '@langchain/core/messages'
import type { AnyStream } from '@langchain/react'
import { Provider, createStore } from 'jotai'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { artifactKeys } from '@/lib/api/artifacts'
import { chatArtifactsAtom } from '@/lib/stores/chat-artifacts'
import { chatRightRailAtom, type RightRailState } from '@/lib/stores/chat-right-rail'
import type { FileEventPayload } from '@/lib/types'
import { protocolArtifactPayload, useLangGraphArtifactEffects } from '../artifact-events'

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

function protocolEvent(payload: FileEventPayload, eventId = 'event-file-1') {
  return {
    type: 'event',
    method: 'custom',
    event_id: eventId,
    seq: 7,
    run_id: 'run-1',
    params: {
      namespace: [],
      data: { name: 'file_event', payload },
    },
  }
}

function artifactEffectsFixture() {
  const store = createStore()
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const stream = { kind: 'stream' } as unknown as AnyStream
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <Provider store={store}>{children}</Provider>
    </QueryClientProvider>
  )
  const useEffects = (messages: readonly BaseMessage[] = []) =>
    useLangGraphArtifactEffects({ stream, conversationId: 'conversation-1', messages })
  return { store, queryClient, stream, wrapper, useEffects }
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

  it('applies v3 artifact custom events to stores, right rail, queries, and live messages', async () => {
    const { store, queryClient, wrapper, useEffects } = artifactEffectsFixture()
    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries')
    const assistantMessage = new AIMessage({ id: 'assistant-1', content: '초안입니다.' })

    const { result } = renderHook(useEffects, { wrapper, initialProps: [assistantMessage] })

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
    await waitFor(() => {
      expect(queryClient.getQueryData(artifactKeys.conversation('conversation-1'))).toEqual([
        artifactSummary(artifact()),
      ])
      expect(invalidateSpy).toHaveBeenCalledWith({
        queryKey: artifactKeys.conversation('conversation-1'),
        exact: true,
      })
    })
    expect((result.current[0] as { artifacts?: unknown[] }).artifacts).toEqual([
      artifactSummary(artifact()),
    ])
  })

  it.each<RightRailState>([
    { mode: 'artifacts', artifacts: { conversationId: 'conversation-1', view: 'list' } },
    {
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        selectedArtifactId: 'chosen',
        view: 'preview',
      },
    },
    {
      mode: 'outline',
      outline: { conversationId: 'conversation-1', messageId: 'message-1', content: 'Outline' },
    },
    { mode: 'none' },
  ])('preserves the user-selected $mode rail when a later artifact arrives', (chosenRail) => {
    const { store, wrapper, useEffects } = artifactEffectsFixture()
    renderHook(useEffects, { wrapper })
    const options: ChannelEffectOptions = mocks.useChannelEffect.mock.calls[0][2]
    act(() => options.onEvent(protocolEvent(artifact())))
    act(() => store.set(chatRightRailAtom, chosenRail))

    act(() => options.onEvent(protocolEvent(artifact({ id: 'artifact-2' }), 'later-event')))

    expect(store.get(chatRightRailAtom)).toBe(chosenRail)
    expect(store.get(chatArtifactsAtom)['conversation-1']?.items).toHaveLength(2)
  })

  it('indexes a known artifact replay after remount without reopening the rail', () => {
    const { store, wrapper, useEffects } = artifactEffectsFixture()
    store.set(chatArtifactsAtom, {
      'conversation-1': { items: [artifactSummary(artifact())], selectedArtifactId: 'artifact-1' },
    })
    const { result } = renderHook(useEffects, {
      wrapper,
      initialProps: [new AIMessage({ id: 'assistant-1', content: 'Done' })],
    })
    const options: ChannelEffectOptions = mocks.useChannelEffect.mock.calls[0][2]

    act(() => options.onEvent(protocolEvent(artifact())))

    expect(store.get(chatRightRailAtom)).toEqual({ mode: 'none' })
    expect(result.current[0]?.artifacts).toEqual([artifactSummary(artifact())])
  })

  it('opens a new conversation artifact after the previous conversation rail was closed', () => {
    const { store, stream, wrapper } = artifactEffectsFixture()
    const { rerender } = renderHook(
      (conversationId: string) =>
        useLangGraphArtifactEffects({ stream, conversationId, messages: [] }),
      { wrapper, initialProps: 'conversation-1' },
    )
    const options: ChannelEffectOptions = mocks.useChannelEffect.mock.calls[0][2]
    act(() => options.onEvent(protocolEvent(artifact())))
    act(() => store.set(chatRightRailAtom, { mode: 'none' }))
    rerender('conversation-2')
    const latestOptions: ChannelEffectOptions = mocks.useChannelEffect.mock.lastCall?.[2]

    act(() =>
      latestOptions.onEvent(
        protocolEvent(
          artifact({
            conversation_id: 'conversation-2',
            id: 'new-artifact',
          }),
          'new-conversation-event',
        ),
      ),
    )

    expect(store.get(chatRightRailAtom)).toEqual({
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-2',
        selectedArtifactId: 'new-artifact',
        view: 'preview',
      },
    })
  })

  it('keeps consecutive stream artifacts in the conversation query cache', async () => {
    const { store, queryClient, wrapper, useEffects } = artifactEffectsFixture()

    renderHook(useEffects, { wrapper })

    const effectOptions = mocks.useChannelEffect.mock.calls[0]?.[2] as
      | ChannelEffectOptions
      | undefined
    const report = artifact()
    const notes = artifact({
      id: 'artifact-2',
      path: 'notes.txt',
      display_name: 'notes.txt',
      version_id: 'version-2',
      extension: 'txt',
      mime_type: 'text/plain',
      artifact_kind: 'document',
    })

    act(() => {
      effectOptions?.onEvent(protocolEvent(report, 'event-file-1'))
      effectOptions?.onEvent(protocolEvent(notes, 'event-file-2'))
    })

    await waitFor(() => {
      const cached = queryClient.getQueryData<FileEventPayload[]>(
        artifactKeys.conversation('conversation-1'),
      )
      expect(cached?.map((item) => item.id).sort()).toEqual(['artifact-1', 'artifact-2'])
    })
    expect(store.get(chatRightRailAtom)).toEqual({
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        selectedArtifactId: 'artifact-2',
        view: 'preview',
      },
    })
    expect(
      store
        .get(chatArtifactsAtom)
        ['conversation-1']?.items.map((item) => item.id)
        .sort(),
    ).toEqual(['artifact-1', 'artifact-2'])
  })
})
