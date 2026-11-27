import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createStore, Provider } from 'jotai'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { artifactKeys, listConversationArtifacts } from '@/lib/api/artifacts'
import { ApiError } from '@/lib/api/errors'
import { useConversationArtifacts } from '@/lib/hooks/use-conversation-artifacts'
import { conversationQueryKeys } from '@/lib/query-keys/conversations'
import { chatArtifactsAtom } from '@/lib/stores/chat-artifacts'
import {
  chatExcludedCapabilitiesAtom,
  dismissExcludedCapabilitiesAtom,
} from '@/lib/stores/chat-excluded-capabilities'
import { chatMemoryRecallAtom } from '@/lib/stores/chat-memory-recall'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import { latestTurnUsageAtom, sessionTokenUsageAtom } from '@/lib/stores/chat-store'
import type { ArtifactSummary, Message } from '@/lib/types'
import { useMoldyAgUiActivityEffects } from '../activity-effects'
import { usageActivityBreakdown } from '../activity-values'

vi.mock('next-intl', () => ({ useTranslations: () => (key: string) => key }))
vi.mock('@/lib/api/artifacts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/artifacts')>()),
  listConversationArtifacts: vi.fn(),
}))

beforeEach(() => {
  vi.mocked(listConversationArtifacts).mockReset().mockResolvedValue([])
})

const conversationId = '80fddd9b-854a-4706-97c8-b0a9ff69beba'
const runId = '126a1ba0-60cf-4305-8489-e08c27b73fad'
const nextRunId = '10f16fea-790d-49c4-90dc-3259f6dee60d'
const date = '2026-11-27T00:00:00Z'
type Snapshot = Parameters<typeof useMoldyAgUiActivityEffects>[1][number]
type Options = NonNullable<Parameters<typeof useMoldyAgUiActivityEffects>[2]>
type EffectsProps = { activities: Snapshot[]; options: Options }

function activity(
  activityType: string,
  messageId: string,
  content: Record<string, unknown>,
  subagentRunId?: string,
): Snapshot {
  return {
    type: 'ACTIVITY_SNAPSHOT',
    physicalRunId: runId,
    activityType,
    messageId,
    content,
    subagentRunId,
  }
}

function providers() {
  const store = createStore()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <Provider store={store}>{children}</Provider>
    </QueryClientProvider>
  )
  return { store, client, wrapper }
}

function mount(initialProps: EffectsProps) {
  const { store, client, wrapper } = providers()
  const view = renderHook(
    ({ activities, options }: EffectsProps) =>
      useMoldyAgUiActivityEffects(conversationId, activities, options),
    { initialProps, wrapper },
  )
  return { client, store, ...view }
}

function file(id: string, versionId: string): ArtifactSummary {
  const url = '/api/conversations/' + conversationId + '/artifacts/' + id
  return {
    id,
    conversation_id: conversationId,
    agent_id: '4dbe7d93-6f62-4b17-bd10-1659b98ca152',
    run_id: runId,
    assistant_msg_id: runId,
    tool_call_id: 'write-report',
    source_tool_name: 'write_file',
    linked_message_ids: ['bd4faf30-8cea-4d28-9fed-70045a9bcb27'],
    display_name: 'report.md',
    path: 'report.md',
    mime_type: 'text/markdown',
    extension: 'md',
    artifact_kind: 'markdown',
    agent_name: '',
    conversation_title: null,
    size_bytes: 12,
    sha256: 'a'.repeat(64),
    status: 'ready',
    is_favorite: false,
    preview_count: 0,
    download_count: 0,
    version_id: versionId,
    version_number: 1,
    created_at: date,
    updated_at: date,
    url,
    preview_url: url + '/content',
    download_url: url + '/download',
  }
}

describe('AG-UI conversation activity effects', () => {
  it('retains saved token counts when the REST message has no price or timing', () => {
    expect(
      usageActivityBreakdown({
        prompt_tokens: 9,
        completion_tokens: 4,
        cache_read_tokens: 2,
        cache_creation_tokens: 0,
        estimated_cost: null,
        ttft_ms: null,
        generation_ms: null,
        tokens_per_second: null,
      }),
    ).toEqual({
      prompt_tokens: 9,
      completion_tokens: 4,
      cache_read_tokens: 2,
      cache_creation_tokens: 0,
    })
  })
  it('keeps streamed files during the initial lookup and hides cached rows after a 403, including on remount', async () => {
    const { store, client, wrapper } = providers()
    const key = artifactKeys.conversation(conversationId)
    const cached = file(
      'd2378687-17c4-4b49-bfe1-cf12914f570e',
      '98553d89-1416-49e9-a245-ca4ba57dc90f',
    )
    store.set(chatArtifactsAtom, {
      [conversationId]: { items: [cached], selectedArtifactId: cached.id },
    })
    vi.mocked(listConversationArtifacts).mockResolvedValue([cached])
    const first = renderHook(() => useConversationArtifacts(conversationId), { wrapper })
    expect(first.result.current.isLoading).toBe(true)
    expect(first.result.current.data).toBeUndefined()
    expect(store.get(chatArtifactsAtom)[conversationId]?.selectedArtifactId).toBe(cached.id)
    await waitFor(() => expect(first.result.current.data).toEqual([cached]))

    const denied = new ApiError(403, 'forbidden', 'Access denied')
    vi.mocked(listConversationArtifacts).mockRejectedValue(denied)
    await act(() => client.invalidateQueries({ queryKey: key, exact: true }))
    await waitFor(() => expect(first.result.current.error).toBe(denied))
    expect(client.getQueryData(key)).toEqual([cached])
    first.unmount()
    const { result } = renderHook(() => useConversationArtifacts(conversationId), { wrapper })
    expect(result.current.data).toBeUndefined()
    expect(store.get(chatArtifactsAtom)[conversationId]?.items).toEqual([])

    vi.mocked(listConversationArtifacts).mockResolvedValue([cached])
    await act(() => client.invalidateQueries({ queryKey: key, exact: true }))
    await waitFor(() =>
      expect(store.get(chatArtifactsAtom)[conversationId]?.items).toEqual([cached]),
    )
    expect(result.current.data).toEqual([cached])
  })

  it('opens a fresh authorized file once and preserves a later panel choice on replay', async () => {
    const restoredFile = file(
      '1975bdf0-e0df-4896-aff7-a0448a014830',
      '8e48848a-ebb1-494b-82db-d217ac616e6f',
    )
    const freshFile = file(
      'd2378687-17c4-4b49-bfe1-cf12914f570e',
      '98553d89-1416-49e9-a245-ca4ba57dc90f',
    )
    const restored = activity('moldy.file_event', 'file-history', {
      id: restoredFile.id,
      version_id: restoredFile.version_id,
      op: 'created',
    })
    const fresh = activity('moldy.file_event', 'file-live', {
      id: freshFile.id,
      version_id: freshFile.version_id,
      op: 'created',
    })
    const lookup = vi.mocked(listConversationArtifacts).mockResolvedValue([restoredFile])
    const { store, rerender } = mount({ activities: [restored], options: { runId } })
    await waitFor(() =>
      expect(store.get(chatArtifactsAtom)[conversationId]?.items.map((item) => item.id)).toEqual([
        restoredFile.id,
      ]),
    )
    expect(store.get(chatRightRailAtom)).toEqual({ mode: 'none' })

    const options = { runId, liveActivityIds: new Set([fresh.messageId]) }
    lookup.mockResolvedValue([restoredFile, freshFile])
    rerender({ activities: [restored, fresh], options })
    await waitFor(() =>
      expect(store.get(chatRightRailAtom)).toMatchObject({
        mode: 'artifacts',
        artifacts: { conversationId, selectedArtifactId: freshFile.id, view: 'preview' },
      }),
    )
    act(() => store.set(chatRightRailAtom, { mode: 'none' }))
    rerender({ activities: [restored, { ...fresh, content: { ...fresh.content } }], options })
    expect(store.get(chatRightRailAtom)).toEqual({ mode: 'none' })
    expect(store.get(chatArtifactsAtom)[conversationId]?.items).toHaveLength(2)
  })

  it('shows a file only for a matching authorized row and version', async () => {
    const descriptor = file(
      'd2378687-17c4-4b49-bfe1-cf12914f570e',
      '98553d89-1416-49e9-a245-ca4ba57dc90f',
    )
    vi.mocked(listConversationArtifacts).mockResolvedValue([descriptor])
    const foreign = activity('moldy.file_event', 'other-thread', {
      id: '3add1809-9229-455a-b940-5746c5ce2eb8',
      version_id: descriptor.version_id,
      op: 'created',
    })
    const wrongVersion = activity('moldy.file_event', 'other-version', {
      id: descriptor.id,
      version_id: '7e466e57-7b31-406a-b923-f02f0453b37f',
      op: 'created',
    })
    const { store, client } = mount({
      activities: [foreign, wrongVersion],
      options: { runId, liveActivityIds: new Set([foreign.messageId, wrongVersion.messageId]) },
    })
    await waitFor(() =>
      expect(client.getQueryState(artifactKeys.conversation(conversationId))?.status).toBe(
        'success',
      ),
    )
    expect(listConversationArtifacts).toHaveBeenCalledWith(conversationId)
    expect(store.get(chatRightRailAtom)).toEqual({ mode: 'none' })
    expect(store.get(chatArtifactsAtom)[conversationId]?.items ?? []).toEqual([])
  })

  it.each(['removed', 'denied'] as const)(
    'removes hydrated file metadata after a %s lookup',
    async (outcome) => {
      const saved = file(
        'd2378687-17c4-4b49-bfe1-cf12914f570e',
        '98553d89-1416-49e9-a245-ca4ba57dc90f',
      )
      const lookup = vi.mocked(listConversationArtifacts).mockResolvedValue([saved])
      const pointer = activity('moldy.file_event', 'previous-file', {
        id: saved.id,
        version_id: saved.version_id,
        op: 'created',
      })
      const { client, store } = mount({ activities: [pointer], options: { runId } })
      await waitFor(() =>
        expect(store.get(chatArtifactsAtom)[conversationId]?.items[0]?.id).toBe(saved.id),
      )
      if (outcome === 'denied') lookup.mockRejectedValue(new Error('Access denied'))
      else lookup.mockResolvedValue([])
      await act(() =>
        client.invalidateQueries({
          queryKey: artifactKeys.conversation(conversationId),
          exact: true,
        }),
      )
      await waitFor(() =>
        expect(store.get(chatArtifactsAtom)[conversationId]?.items ?? []).toEqual([]),
      )
    },
  )

  it('merges a saved message and its live usage once and keeps child usage out of the root context gauge', () => {
    const breakdown = {
      prompt_tokens: 50,
      completion_tokens: 7,
      cache_creation_tokens: 0,
      cache_read_tokens: 0,
    }
    const message: Message = {
      id: 'bd4faf30-8cea-4d28-9fed-70045a9bcb27',
      runtime_message_id: 'model-answer',
      conversation_id: conversationId,
      created_at: date,
      role: 'assistant',
      content: 'done',
      tool_calls: null,
      tool_call_id: null,
      usage: { ...breakdown, estimated_cost: 0.125 },
    }
    const root = activity('moldy.usage', 'usage-root', {
      ...breakdown,
      run_id: runId,
      assistant_msg_id: 'model-answer',
      ttft_ms: 30,
    })
    const child = activity(
      'moldy.usage',
      'usage-child',
      {
        ...breakdown,
        prompt_tokens: 9,
        run_id: runId,
        assistant_msg_id: 'child-answer',
      },
      'child-job',
    )
    const options = { runId, serverMessages: [message] }
    const { store, rerender } = mount({ activities: [root], options })
    expect(store.get(sessionTokenUsageAtom)).toEqual({
      inputTokens: 50,
      outputTokens: 7,
      cost: 0.125,
    })
    expect(store.get(latestTurnUsageAtom)).toMatchObject({ prompt_tokens: 50, ttft_ms: 30 })

    rerender({ activities: [{ ...root }, child], options })
    expect(store.get(latestTurnUsageAtom)).toMatchObject({ prompt_tokens: 50, ttft_ms: 30 })
  })

  it('clears exclusions on a clean run and restores the selected branch’s dismissal', () => {
    const groups = [{ type: 'tool', reason: 'denied', count: 2 }]
    const denied = activity('moldy.excluded_capabilities', 'exclusions', { items: groups })
    const { store, rerender } = mount({ activities: [denied], options: { runId } })
    expect(store.get(chatExcludedCapabilitiesAtom)[conversationId]?.notice).toMatchObject({
      groups,
      runKey: runId,
    })

    act(() => store.set(dismissExcludedCapabilitiesAtom, conversationId))
    rerender({ activities: [{ ...denied }], options: { runId: nextRunId } })
    expect(store.get(chatExcludedCapabilitiesAtom)[conversationId]?.notice).toBeNull()
    rerender({ activities: [{ ...denied }], options: { runId } })
    expect(store.get(chatExcludedCapabilitiesAtom)[conversationId]?.notice).toMatchObject({
      runKey: runId,
      groups,
      dismissed: true,
    })
  })

  it('keeps recalled text redacted and removes a deleted memory on replay', () => {
    const recalled = activity('moldy.memory_recalled', 'recall', {
      memories: [{ id: 'memory-1', scope: 'user', content: 'private provider text', path: 'auto' }],
    })
    const deleted = activity('moldy.memory_deleted', 'deletion', {
      memory_id: 'memory-1',
      scope: 'user',
      content: '<redacted>',
    })
    const { store, rerender } = mount({ activities: [recalled], options: { runId } })
    expect(store.get(chatMemoryRecallAtom)[conversationId]).toMatchObject([
      { id: 'memory-1', scope: 'user', content: '<redacted>' },
    ])

    rerender({ activities: [recalled, deleted], options: { runId } })
    expect(store.get(chatMemoryRecallAtom)[conversationId]).toEqual([])
  })

  it('invalidates the current conversation’s Steer query without changing saved receipts', () => {
    const otherId = 'af24a658-7925-4962-9d79-43acb98d7753'
    const currentKey = conversationQueryKeys.steers(conversationId)
    const otherKey = conversationQueryKeys.steers(otherId)
    const { client, rerender } = mount({ activities: [], options: { runId } })
    const receipts = [{ id: 'saved', status: 'queued' }]
    client.setQueryData(currentKey, receipts)
    client.setQueryData(otherKey, receipts)
    rerender({
      activities: [
        activity('moldy.run_steer', 'new-receipt', {
          steer_id: 'saved',
          run_id: runId,
          status: 'applied',
          conversation_id: conversationId,
        }),
        activity('moldy.run_steer', 'wrong-conversation', {
          steer_id: 'other',
          run_id: runId,
          status: 'applied',
          conversation_id: otherId,
        }),
      ],
      options: { runId },
    })
    expect(client.getQueryState(currentKey)?.isInvalidated).toBe(true)
    expect(client.getQueryData(currentKey)).toEqual(receipts)
    expect(client.getQueryState(otherKey)?.isInvalidated).toBe(false)
  })
})
