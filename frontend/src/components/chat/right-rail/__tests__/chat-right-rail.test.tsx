import { Provider, createStore } from 'jotai'
import { fireEvent } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '../../../../../tests/test-utils'
import { chatArtifactsAtom } from '@/lib/stores/chat-artifacts'
import { chatRightRailAtom, chatRightRailWidthAtom } from '@/lib/stores/chat-right-rail'
import type { ArtifactSummary } from '@/lib/types'
import { ChatRightRail } from '../chat-right-rail'

vi.mock('../artifact-panel-content', () => ({
  ArtifactPanelContent: () => <div data-testid="artifact-panel-content" />,
}))

function installAnimationFrameStub(): void {
  Object.defineProperty(window, 'requestAnimationFrame', {
    configurable: true,
    value: (callback: FrameRequestCallback) => {
      callback(0)
      return 1
    },
  })
  Object.defineProperty(window, 'cancelAnimationFrame', {
    configurable: true,
    value: () => {},
  })
  Object.defineProperty(window, 'innerWidth', {
    configurable: true,
    value: 1366,
  })
}

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
  beforeEach(() => {
    installAnimationFrameStub()
    window.localStorage.clear()
  })

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

    const aside = container.querySelector('aside')
    expect(aside).toHaveClass('relative')
    expect(aside).toHaveStyle({
      '--chat-right-rail-width': '384px',
      width: 'var(--chat-right-rail-width)',
    })
    expect(container.querySelector('[data-slot="chat-right-rail-frame"]')).toHaveClass('w-full')
    expect(container.querySelector('[role="dialog"] > div')).toHaveClass(
      'moldy-artifact-mobile-layer',
    )
    expect(container.querySelector('[role="dialog"] > div')).not.toHaveClass(
      'moldy-right-rail-mobile',
    )
  })

  it('resizes the desktop rail and persists the last stable width', () => {
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

    const aside = container.querySelector('aside')
    const handle = screen.getByRole('separator', { name: '파일 패널 크기 조절' })

    fireEvent.pointerDown(handle, { clientX: 400, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 240, pointerId: 1 })
    expect(aside).toHaveStyle({ '--chat-right-rail-width': '544px' })

    fireEvent.pointerUp(handle, { clientX: 240, pointerId: 1 })

    expect(store.get(chatRightRailWidthAtom)).toBe(544)
    expect(window.localStorage.getItem('moldy.chatRightRail.widthPx')).toBe('544')
  })

  it('reclamps the desktop rail when the viewport narrows', () => {
    const store = createStore()
    store.set(chatRightRailWidthAtom, 720)
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

    const aside = container.querySelector('aside')
    expect(aside).toHaveStyle({ '--chat-right-rail-width': '720px' })

    Object.defineProperty(window, 'innerWidth', {
      configurable: true,
      value: 1000,
    })
    fireEvent(window, new Event('resize'))

    expect(aside).toHaveStyle({ '--chat-right-rail-width': '480px' })
    expect(screen.getByRole('separator', { name: '파일 패널 크기 조절' })).toHaveAttribute(
      'aria-valuemax',
      '480',
    )
  })

  it('closes below the collapse threshold without overwriting the last stable width', () => {
    const store = createStore()
    store.set(chatRightRailWidthAtom, 420)
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

    const aside = container.querySelector('aside')
    const handle = screen.getByRole('separator', { name: '파일 패널 크기 조절' })

    fireEvent.pointerDown(handle, { clientX: 400, pointerId: 1 })
    fireEvent.pointerMove(handle, { clientX: 700, pointerId: 1 })

    expect(aside).toHaveStyle({ '--chat-right-rail-width': '120px' })
    expect(handle).toHaveAttribute('data-collapse-preview', 'true')

    fireEvent.pointerUp(handle, { clientX: 700, pointerId: 1 })

    expect(store.get(chatRightRailAtom)).toEqual({ mode: 'none' })
    expect(store.get(chatRightRailWidthAtom)).toBe(420)
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
