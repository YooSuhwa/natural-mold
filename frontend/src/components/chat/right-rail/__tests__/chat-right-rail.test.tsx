import { Provider, createStore } from 'jotai'
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '../../../../../tests/test-utils'
import { chatArtifactsAtom } from '@/lib/stores/chat-artifacts'
import { chatRightRailAtom } from '@/lib/stores/chat-right-rail'
import type { ArtifactSummary } from '@/lib/types'
import { ChatRightRail } from '../chat-right-rail'

vi.mock('../artifact-panel-content', () => ({
  ArtifactPanelContent: () => <div data-testid="artifact-panel-content" />,
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

describe('ChatRightRail', () => {
  it('uses the selected file name as the artifact preview title', () => {
    const store = createStore()
    store.set(chatArtifactsAtom, {
      'conversation-1': {
        items: [
          artifact({ id: 'report', display_name: 'report.md', path: 'report.md' }),
          artifact({
            id: 'code',
            display_name: 'example.py',
            path: 'code/example.py',
            artifact_kind: 'code',
            extension: 'py',
            mime_type: 'text/x-python',
          }),
        ],
        selectedArtifactId: 'code',
      },
    })
    store.set(chatRightRailAtom, {
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        selectedArtifactId: 'code',
        view: 'preview',
      },
    })

    render(
      <Provider store={store}>
        <ChatRightRail conversationId="conversation-1" />
      </Provider>,
    )

    expect(screen.getAllByRole('heading', { name: 'example.py' }).length).toBeGreaterThan(0)
    expect(screen.queryByRole('heading', { name: '파일' })).not.toBeInTheDocument()
  })

  it('falls back to the current store selection when payload points to a deleted artifact', () => {
    const store = createStore()
    store.set(chatArtifactsAtom, {
      'conversation-1': {
        items: [
          artifact({ id: 'report', display_name: 'report.md', path: 'report.md' }),
          artifact({
            id: 'code',
            display_name: 'example.py',
            path: 'code/example.py',
            artifact_kind: 'code',
            extension: 'py',
            mime_type: 'text/x-python',
          }),
        ],
        selectedArtifactId: 'code',
      },
    })
    store.set(chatRightRailAtom, {
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        selectedArtifactId: 'deleted-artifact',
        view: 'preview',
      },
    })

    render(
      <Provider store={store}>
        <ChatRightRail conversationId="conversation-1" />
      </Provider>,
    )

    expect(screen.getAllByRole('heading', { name: 'example.py' }).length).toBeGreaterThan(0)
    expect(screen.queryByRole('heading', { name: '파일' })).not.toBeInTheDocument()
  })

  it('uses stable desktop rail and mobile full-screen classes for artifact shell', () => {
    const store = createStore()
    store.set(chatRightRailAtom, {
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        view: 'list',
      },
    })

    const { container } = render(
      <Provider store={store}>
        <ChatRightRail conversationId="conversation-1" />
      </Provider>,
    )

    expect(container.querySelector('aside')).toHaveClass('moldy-right-rail')
    expect(container.querySelector('[role="dialog"] > div')).toHaveClass(
      'moldy-artifact-mobile-layer',
    )
    expect(container.querySelector('[role="dialog"] > div')).not.toHaveClass(
      'moldy-right-rail-mobile',
    )
  })

  it('keeps artifact close controls on the left for mobile list and preview headers', () => {
    const listStore = createStore()
    listStore.set(chatRightRailAtom, {
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        view: 'list',
      },
    })

    const { container: listContainer } = render(
      <Provider store={listStore}>
        <ChatRightRail conversationId="conversation-1" />
      </Provider>,
    )

    const listHeader = listContainer.querySelector('[role="dialog"] header')
    const listCloseButtons = Array.from(
      listHeader?.querySelectorAll('button[aria-label="Close panel"]') ?? [],
    )
    expect(listCloseButtons[0]).toHaveClass('md:hidden')
    expect(listHeader?.firstElementChild).toBe(listCloseButtons[0])
    expect(listCloseButtons[1]).toHaveClass('hidden', 'md:inline-flex')

    const previewStore = createStore()
    previewStore.set(chatArtifactsAtom, {
      'conversation-1': {
        items: [artifact({ id: 'report', display_name: 'report.md', path: 'report.md' })],
        selectedArtifactId: 'report',
      },
    })
    previewStore.set(chatRightRailAtom, {
      mode: 'artifacts',
      artifacts: {
        conversationId: 'conversation-1',
        selectedArtifactId: 'report',
        view: 'preview',
      },
    })

    const { container: previewContainer } = render(
      <Provider store={previewStore}>
        <ChatRightRail conversationId="conversation-1" />
      </Provider>,
    )

    const previewHeader = previewContainer.querySelector('[role="dialog"] header')
    const previewCloseButtons = Array.from(
      previewHeader?.querySelectorAll('button[aria-label="Close panel"]') ?? [],
    )
    expect(previewCloseButtons[0]).toHaveClass('md:hidden')
    expect(previewHeader?.firstElementChild).toBe(previewCloseButtons[0])
    expect(previewCloseButtons.at(-1)).toHaveClass('hidden', 'md:inline-flex')
  })
})
