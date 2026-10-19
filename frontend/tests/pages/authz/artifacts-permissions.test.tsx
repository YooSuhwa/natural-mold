import { render, screen } from '../../test-utils'
import { ArtifactLibraryContent } from '@/components/artifacts/artifact-library-content'
import { ArtifactPreview } from '@/components/chat/artifacts/artifact-preview'
import { ApiError } from '@/lib/api/errors'
import type { ArtifactSummary } from '@/lib/types'

const mocks = vi.hoisted(() => ({ library: vi.fn(), content: vi.fn() }))

vi.mock('@/lib/hooks/use-agents', () => ({ useAgentSummaries: () => ({ data: [] }) }))
vi.mock('@/lib/hooks/use-artifact-library', () => ({
  useArtifactLibrary: () => mocks.library(),
  useArtifactLibraryStats: () => ({ data: undefined }),
  useRecentArtifacts: () => ({ data: [] }),
  useRecordArtifactOpened: () => ({ mutate: vi.fn() }),
  useSetArtifactFavorite: () => ({ mutate: vi.fn() }),
}))
vi.mock('@/lib/api/artifacts', async (importActual) => ({
  ...(await importActual<typeof import('@/lib/api/artifacts')>()),
  getArtifactTextContent: () => mocks.content(),
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
    download_url: '/api/conversations/conversation-1/artifacts/artifact-1/download',
    ...overrides,
  }
}

function libraryResult(overrides: Record<string, unknown>) {
  return {
    data: undefined,
    isLoading: false,
    isError: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
    refetch: vi.fn(),
    ...overrides,
  }
}

describe('artifact library (plan 13.9: own artifacts of the current organization)', () => {
  it('lists the rows the server returned for me', () => {
    mocks.library.mockReturnValue(
      libraryResult({ data: { pages: [{ items: [artifact()], next_cursor: null }] } }),
    )
    mocks.content.mockResolvedValue({ text: '# hi', truncated: false })
    render(<ArtifactLibraryContent />)
    expect(screen.getAllByText('report.md').length).toBeGreaterThan(0)
  })

  it('shows the shared error state instead of an empty list when loading fails', () => {
    mocks.library.mockReturnValue(
      libraryResult({ isError: true, error: new ApiError(503, 'authz_unavailable', 'x') }),
    )
    render(<ArtifactLibraryContent />)
    expect(screen.getByText('권한을 확인하지 못했어요')).toBeInTheDocument()
  })
})

describe('artifact preview of another organization’s id', () => {
  it('renders the not-found state', async () => {
    mocks.content.mockRejectedValue(new ApiError(404, 'ARTIFACT_NOT_FOUND', 'x'))
    render(<ArtifactPreview artifact={artifact({ id: 'foreign' })} />)
    expect(await screen.findByText('찾을 수 없어요')).toBeInTheDocument()
  })
})
