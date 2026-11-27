import {
  AssistantRuntimeProvider,
  ThreadPrimitive,
  useExternalStoreRuntime,
  type ThreadMessageLike,
} from '@assistant-ui/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { createStore, Provider } from 'jotai'
import type { ReactNode } from 'react'
import { expect, it, vi } from 'vitest'
import { render, screen, userEvent } from '../../../../tests/test-utils'
import { StreamingMessageLoadingIndicator } from '@/components/chat/assistant-message-loading'
import { artifactKeys, listConversationArtifacts } from '@/lib/api/artifacts'
import { ApiError } from '@/lib/api/errors'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import type { ArtifactSummary } from '@/lib/types'
import { AgUiActivityProvider, useAgUiStateFiles } from '../activity-context'
import type { AgUiActivitySnapshot } from '../chat-history-types'

vi.mock('@/lib/api/artifacts', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/artifacts')>()),
  listConversationArtifacts: vi.fn(),
}))

function uuid(n: number) {
  return '7a231fdc-73ea-42f1-a35d-' + String(n).padStart(12, '0')
}

const conversationId = uuid(10)
const runId = uuid(11)

function file(n: number, run = runId): ArtifactSummary {
  const id = uuid(n)
  const url = '/api/conversations/' + conversationId + '/artifacts/' + id
  return {
    id,
    conversation_id: conversationId,
    agent_id: uuid(12),
    assistant_msg_id: run,
    run_id: run,
    display_name: 'report-' + n + '.md',
    path: 'out/report-' + n + '.md',
    mime_type: 'text/markdown',
    artifact_kind: 'markdown',
    size_bytes: 19,
    sha256: 'b'.repeat(64),
    status: 'ready',
    is_favorite: false,
    preview_count: 0,
    download_count: 0,
    version_id: uuid(n + 100),
    version_number: 1,
    created_at: '2026-11-27T00:00:00Z',
    updated_at: '2026-11-27T00:00:00Z',
    url,
    preview_url: url + '/content',
    download_url: url + '/download',
  }
}

function pointer(row: ArtifactSummary, version = row.version_id): AgUiActivitySnapshot {
  return {
    type: 'ACTIVITY_SNAPSHOT',
    activityType: 'moldy.file_event',
    physicalRunId: row.run_id,
    messageId: 'created-' + row.id,
    content: { id: row.id, version_id: version, op: 'created' },
  }
}

const runningMessages: ThreadMessageLike[] = [
  { id: 'active-assistant', role: 'assistant', content: 'Processing', status: { type: 'running' } },
]

function RunningThread() {
  const runtime = useExternalStoreRuntime({
    messages: runningMessages,
    isRunning: true,
    onNew: async () => {},
    convertMessage: (message) => message,
  })
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root>
        <ThreadPrimitive.Messages>
          {({ message }) => message.role === 'assistant' && <StreamingMessageLoadingIndicator />}
        </ThreadPrimitive.Messages>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  )
}

it('projects authorized root files for the selected run and hides them when the private lookup is denied', async () => {
  const root = file(1)
  const previous = file(2, uuid(13))
  const child = file(3)
  const changed = file(4)
  const activities: AgUiActivitySnapshot[] = [
    pointer(root),
    pointer(previous),
    { ...pointer(child), subagentRunId: 'child-job' },
    pointer(changed, uuid(400)),
  ]
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const store = createStore()
  const key = artifactKeys.conversation(conversationId)
  client.setQueryData(key, [{ ...root, display_name: 'stale.md' }])
  const rows = [{ ...root, content: 'raw virtual contents' }, previous, child, changed]
  const lookup = vi.mocked(listConversationArtifacts).mockResolvedValue(rows)
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <Provider store={store}>
        <AgUiActivityProvider
          conversationId={conversationId}
          physicalRunId={runId}
          activities={activities}
        >
          {children}
        </AgUiActivityProvider>
      </Provider>
    </QueryClientProvider>
  )

  const { result } = renderHook(() => useAgUiStateFiles(), { wrapper })
  expect(result.current?.files).toEqual([])
  await waitFor(() => expect(result.current?.files).toHaveLength(1))
  const item = result.current?.files[0]
  if (!item) throw new Error('Expected the authorized root file')
  expect(item).toMatchObject({
    id: root.id,
    name: root.display_name,
    path: root.path,
    mimeType: root.mime_type,
    sizeBytes: root.size_bytes,
    previewUrl: root.preview_url,
    downloadUrl: root.download_url,
  })
  expect(item).not.toHaveProperty('content')
  expect(lookup.mock.calls).toEqual([[conversationId]])
  act(() => result.current?.openPreview({ ...item, id: child.id }))
  expect(store.get(chatRightRailAtom)).toEqual({ mode: 'none' })
  act(() => result.current?.openPreview(item))
  expect(store.get(chatRightRailAtom)).toMatchObject({
    mode: 'artifacts',
    artifacts: { conversationId, selectedArtifactId: root.id, view: 'preview' },
  })

  lookup.mockRejectedValue(new ApiError(403, 'forbidden', 'Access denied'))
  await act(() => client.invalidateQueries({ queryKey: key, exact: true }))
  await waitFor(() => expect(result.current?.files).toEqual([]))
  expect(client.getQueryState(key)?.status).toBe('error')
  expect(client.getQueryData(key)).toEqual(rows)
  act(() => store.set(chatRightRailAtom, { mode: 'none' }))
  act(() => result.current?.openPreview(item))
  expect(store.get(chatRightRailAtom)).toEqual({ mode: 'none' })
})

it('opens the displayed root artifact from a files-only streaming message', async () => {
  const user = userEvent.setup()
  const store = createStore()
  const root = file(1)
  vi.mocked(listConversationArtifacts).mockResolvedValue([root])
  render(
    <Provider store={store}>
      <AgUiActivityProvider
        conversationId={conversationId}
        physicalRunId={runId}
        activities={[pointer(root)]}
      >
        <RunningThread />
      </AgUiActivityProvider>
    </Provider>,
  )
  const preview = await screen.findByRole('button', { name: root.display_name + ' 미리보기 열기' })
  expect(screen.getByText(root.display_name)).toBeVisible()
  expect(screen.getByText('파일')).toBeVisible()
  expect(screen.queryByText('작업 계획')).not.toBeInTheDocument()
  await user.click(preview)
  expect(store.get(chatRightRailAtom)).toEqual({
    mode: 'artifacts',
    artifacts: { conversationId, selectedArtifactId: root.id, view: 'preview' },
  })
})
