import { Provider, createStore } from 'jotai'
import { describe, expect, it, vi } from 'vitest'
import { render, screen, userEvent } from '../../../../../tests/test-utils'
import { chatArtifactsAtom } from '@/lib/stores/chat-artifacts'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import type { ArtifactSummary } from '@/lib/types'
import { ArtifactPanelContent } from '../artifact-panel-content'

vi.mock('@/lib/hooks/use-conversation-artifacts', () => ({
  useConversationArtifacts: () => ({ isLoading: false }),
}))

vi.mock('@/lib/hooks/use-artifact-library', () => ({
  useRecordArtifactOpened: () => ({ mutate: vi.fn() }),
  useSetArtifactFavorite: () => ({ mutate: vi.fn() }),
}))

vi.mock('@/components/chat/artifacts/artifact-preview', () => ({
  ArtifactPreview: ({ artifact }: { artifact: ArtifactSummary }) => (
    <div data-testid="artifact-preview">preview:{artifact.display_name}</div>
  ),
}))

function artifact(overrides: Partial<ArtifactSummary>): ArtifactSummary {
  return {
    id: 'artifact-1',
    agent_id: 'agent-1',
    conversation_id: 'conversation-1',
    assistant_msg_id: 'run-1',
    run_id: 'run-1',
    tool_call_id: null,
    source_tool_name: 'execute_in_skill',
    path: 'report.md',
    display_name: 'report.md',
    mime_type: 'text/markdown',
    extension: 'md',
    artifact_kind: 'markdown',
    size_bytes: 10,
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

function renderPanel(view: 'list' | 'preview', selectedArtifactId = 'report') {
  const store = createStore()
  const report = artifact({ id: 'report', display_name: 'report.md', path: 'report.md' })
  const code = artifact({
    id: 'code',
    display_name: 'example.py',
    path: 'code/example.py',
    artifact_kind: 'code',
    extension: 'py',
    mime_type: 'text/x-python',
  })
  store.set(chatArtifactsAtom, {
    'conversation-1': {
      items: [report, code],
      selectedArtifactId,
    },
  })

  render(
    <Provider store={store}>
      <ArtifactPanelContent
        payload={{ conversationId: 'conversation-1', selectedArtifactId, view }}
      />
    </Provider>,
  )

  return store
}

function renderPanelWithStalePayload() {
  const store = createStore()
  const report = artifact({ id: 'report', display_name: 'report.md', path: 'report.md' })
  const code = artifact({
    id: 'code',
    display_name: 'example.py',
    path: 'code/example.py',
    artifact_kind: 'code',
    extension: 'py',
    mime_type: 'text/x-python',
  })
  store.set(chatArtifactsAtom, {
    'conversation-1': {
      items: [report, code],
      selectedArtifactId: 'code',
    },
  })

  render(
    <Provider store={store}>
      <ArtifactPanelContent
        payload={{
          conversationId: 'conversation-1',
          selectedArtifactId: 'deleted-artifact',
          view: 'preview',
        }}
      />
    </Provider>,
  )
}

describe('ArtifactPanelContent', () => {
  it('shows only the session file list in list mode', () => {
    renderPanel('list')

    expect(screen.getByText('파일')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /report\.md/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /example\.py/ })).toBeInTheDocument()
    expect(screen.queryByTestId('artifact-preview')).not.toBeInTheDocument()
  })

  it('shows only the selected preview in preview mode', () => {
    renderPanel('preview', 'code')

    expect(screen.queryByText('파일')).not.toBeInTheDocument()
    expect(screen.queryByText('파일 목록')).not.toBeInTheDocument()
    expect(screen.getByTestId('artifact-preview')).toHaveTextContent('preview:example.py')
    expect(screen.queryByRole('button', { name: /report\.md/ })).not.toBeInTheDocument()
  })

  it('falls back to the current store selection when payload points to a deleted artifact', () => {
    renderPanelWithStalePayload()

    expect(screen.getByTestId('artifact-preview')).toHaveTextContent('preview:example.py')
    expect(screen.queryByRole('button', { name: /report\.md/ })).not.toBeInTheDocument()
  })

  it('moves from list mode to preview mode when a file is selected', async () => {
    const store = renderPanel('list')

    await userEvent.click(screen.getByRole('button', { name: /example\.py/ }))

    expect(store.get(chatRightRailAtom)).toEqual({
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        selectedArtifactId: 'code',
        view: 'preview',
      },
    })
  })
})
