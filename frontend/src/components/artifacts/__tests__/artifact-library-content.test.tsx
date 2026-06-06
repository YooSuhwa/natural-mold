import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, userEvent } from '../../../../tests/test-utils'
import type { ArtifactSummary } from '@/lib/types'
import { ArtifactLibraryContent } from '../artifact-library-content'

const mocks = vi.hoisted(() => ({
  fetchNextPage: vi.fn(),
  mutate: vi.fn(),
  useArtifactLibrary: vi.fn(),
  useRecentArtifacts: vi.fn(),
}))

vi.mock('@/lib/hooks/use-agents', () => ({
  useAgentSummaries: () => ({ data: [] }),
}))

vi.mock('@/lib/hooks/use-artifact-library', () => ({
  useArtifactLibrary: mocks.useArtifactLibrary,
  useArtifactLibraryStats: () => ({ data: undefined }),
  useRecentArtifacts: mocks.useRecentArtifacts,
  useRecordArtifactOpened: () => ({ mutate: mocks.mutate }),
  useSetArtifactFavorite: () => ({ mutate: mocks.mutate }),
}))

vi.mock('@/components/chat/artifacts/artifact-preview', () => ({
  ArtifactPreview: ({ artifact }: { artifact: ArtifactSummary | null }) => (
    <div data-testid="artifact-preview">{artifact?.display_name ?? 'empty'}</div>
  ),
}))

function artifact(overrides: Partial<ArtifactSummary> = {}): ArtifactSummary {
  return {
    id: 'artifact-1',
    agent_id: 'agent-1',
    conversation_id: 'conversation-1',
    assistant_msg_id: 'message-1',
    run_id: 'run-1',
    tool_call_id: null,
    source_tool_name: 'execute_in_skill',
    path: 'report.md',
    display_name: 'report.md',
    mime_type: 'text/markdown',
    extension: 'md',
    artifact_kind: 'markdown',
    size_bytes: 120,
    sha256: 'a'.repeat(64),
    status: 'ready',
    is_favorite: false,
    last_opened_at: null,
    preview_count: 0,
    download_count: 0,
    version_id: 'version-1',
    version_number: 1,
    created_at: '2026-06-05T00:00:00Z',
    updated_at: '2026-06-05T00:00:00Z',
    agent_name: null,
    conversation_title: null,
    url: '/api/conversations/conversation-1/artifacts/artifact-1',
    preview_url: '/api/conversations/conversation-1/artifacts/artifact-1/content',
    download_url: '/api/conversations/conversation-1/artifacts/artifact-1/download',
    ...overrides,
  }
}

describe('ArtifactLibraryContent', () => {
  beforeEach(() => {
    mocks.fetchNextPage.mockReset()
    mocks.mutate.mockReset()
    mocks.useArtifactLibrary.mockReset()
    mocks.useRecentArtifacts.mockReset()
    mocks.useRecentArtifacts.mockReturnValue({ data: [] })
  })

  it('requests the next page when more library artifacts are available', async () => {
    mocks.useArtifactLibrary.mockReturnValue({
      data: {
        pages: [
          {
            items: [artifact()],
            next_cursor: 'cursor-2',
            has_more: true,
          },
        ],
      },
      isLoading: false,
      hasNextPage: true,
      isFetchingNextPage: false,
      fetchNextPage: mocks.fetchNextPage,
    })

    render(<ArtifactLibraryContent />)

    await userEvent.click(screen.getByRole('button', { name: '더 보기' }))

    expect(mocks.fetchNextPage).toHaveBeenCalledTimes(1)
  })

  it('keeps the preview empty when the active filters have no matching artifacts', () => {
    mocks.useArtifactLibrary.mockReturnValue({
      data: {
        pages: [
          {
            items: [],
            next_cursor: null,
            has_more: false,
          },
        ],
      },
      isLoading: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: mocks.fetchNextPage,
    })
    mocks.useRecentArtifacts.mockReturnValue({
      data: [artifact({ id: 'recent-artifact', display_name: 'recent.md' })],
    })

    render(<ArtifactLibraryContent />)

    expect(screen.getByText('조건에 맞는 파일이 없습니다.')).toBeInTheDocument()
    expect(screen.getByTestId('artifact-preview')).toHaveTextContent('empty')
  })

  it('clears a recent selection when the active filters exclude it', async () => {
    const recentArtifact = artifact({ id: 'recent-artifact', display_name: 'recent.md' })
    mocks.useArtifactLibrary.mockImplementation((params: { q?: string | null }) => ({
      data: {
        pages: [
          {
            items: params.q
              ? []
              : [artifact({ id: 'library-artifact', display_name: 'library.md' })],
            next_cursor: null,
            has_more: false,
          },
        ],
      },
      isLoading: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: mocks.fetchNextPage,
    }))
    mocks.useRecentArtifacts.mockReturnValue({
      data: [recentArtifact],
    })

    render(<ArtifactLibraryContent />)

    await userEvent.click(screen.getByRole('button', { name: /recent\.md/ }))
    expect(screen.getByTestId('artifact-preview')).toHaveTextContent('recent.md')

    await userEvent.type(screen.getByLabelText('파일 검색'), 'missing')

    expect(screen.getByText('조건에 맞는 파일이 없습니다.')).toBeInTheDocument()
    expect(screen.getByTestId('artifact-preview')).toHaveTextContent('empty')
  })
})
