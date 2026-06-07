import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within } from '../../../../../tests/test-utils'
import { getArtifactTextContent } from '@/lib/api/artifacts'
import type { ArtifactSummary, ArtifactTextContent } from '@/lib/types'
import { ArtifactPreview } from '../artifact-preview'

vi.mock('@/lib/api/artifacts', () => ({
  getArtifactTextContent: vi.fn(),
}))

const mockedGetArtifactTextContent = vi.mocked(getArtifactTextContent)

function artifact(overrides: Partial<ArtifactSummary>): ArtifactSummary {
  return {
    id: 'artifact-1',
    agent_id: 'agent-1',
    conversation_id: 'conversation-1',
    assistant_msg_id: 'run-1',
    run_id: 'run-1',
    tool_call_id: null,
    source_tool_name: 'execute_in_skill',
    path: 'data.csv',
    display_name: 'data.csv',
    mime_type: 'text/csv',
    extension: 'csv',
    artifact_kind: 'data',
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

function content(text: string): ArtifactTextContent {
  return {
    text,
    truncated: false,
    mime_type: 'text/plain',
    size_bytes: text.length,
  }
}

describe('ArtifactPreview data viewers', () => {
  beforeEach(() => {
    mockedGetArtifactTextContent.mockReset()
  })

  it('renders csv content as a table preview', async () => {
    mockedGetArtifactTextContent.mockResolvedValue(content('name,score\nAda,10\nLinus,8'))

    render(<ArtifactPreview artifact={artifact({ extension: 'csv', mime_type: 'text/csv' })} />)

    const table = await screen.findByRole('table')
    expect(within(table).getByRole('columnheader', { name: 'name' })).toBeInTheDocument()
    expect(within(table).getByRole('columnheader', { name: 'score' })).toBeInTheDocument()
    expect(within(table).getByText('Ada')).toBeInTheDocument()
    expect(within(table).getByText('Linus')).toBeInTheDocument()
  })

  it('renders json content as a structured tree preview', async () => {
    mockedGetArtifactTextContent.mockResolvedValue(
      content('{"project":{"name":"Moldy"},"items":[1,true]}'),
    )

    render(
      <ArtifactPreview
        artifact={artifact({
          artifact_kind: 'code',
          extension: 'json',
          mime_type: 'application/json',
        })}
      />,
    )

    await waitFor(() => expect(screen.getByText('project')).toBeInTheDocument())
    expect(screen.getByText('name')).toBeInTheDocument()
    expect(screen.getByText('"Moldy"')).toBeInTheDocument()
    expect(screen.getByText('items')).toBeInTheDocument()
  })

  it('renders yaml and toml content as structured tree previews', async () => {
    mockedGetArtifactTextContent.mockResolvedValueOnce(content('project:\n  name: Moldy\ncount: 2'))
    const { unmount } = render(
      <ArtifactPreview
        artifact={artifact({
          artifact_kind: 'code',
          extension: 'yaml',
          mime_type: 'application/yaml',
        })}
      />,
    )

    await waitFor(() => expect(screen.getByText('project')).toBeInTheDocument())
    expect(screen.getByText('"Moldy"')).toBeInTheDocument()
    expect(screen.getByText('count')).toBeInTheDocument()
    unmount()

    mockedGetArtifactTextContent.mockResolvedValueOnce(
      content('[project]\nname = "Moldy"\ncount = 2'),
    )
    render(
      <ArtifactPreview
        artifact={artifact({
          artifact_kind: 'code',
          extension: 'toml',
          mime_type: 'application/toml',
        })}
      />,
    )

    await waitFor(() => expect(screen.getByText('project')).toBeInTheDocument())
    expect(screen.getByText('"Moldy"')).toBeInTheDocument()
    expect(screen.getByText('count')).toBeInTheDocument()
  })

  it('renders raw text in code preview mode', async () => {
    mockedGetArtifactTextContent.mockResolvedValue(content('const answer = 42'))

    render(
      <ArtifactPreview
        artifact={artifact({
          artifact_kind: 'code',
          extension: 'ts',
          mime_type: 'text/typescript',
        })}
        previewMode="code"
      />,
    )

    await waitFor(() => expect(screen.getByText('const answer = 42')).toBeInTheDocument())
  })

  it('uses preview urls for embeddable pdf and media artifacts', () => {
    const { container, rerender } = render(
      <ArtifactPreview
        artifact={artifact({
          artifact_kind: 'pdf',
          extension: 'pdf',
          mime_type: 'application/pdf',
          preview_url: '/api/artifacts/artifact-1/content',
          download_url: '/api/artifacts/artifact-1/download',
        })}
      />,
    )

    expect(container.querySelector('iframe')?.getAttribute('src')).toBe(
      'http://localhost:8001/api/artifacts/artifact-1/content',
    )

    rerender(
      <ArtifactPreview
        artifact={artifact({
          artifact_kind: 'video',
          extension: 'mp4',
          mime_type: 'video/mp4',
          preview_url: '/api/artifacts/artifact-1/content',
          download_url: '/api/artifacts/artifact-1/download',
        })}
      />,
    )
    expect(container.querySelector('video')?.getAttribute('src')).toBe(
      'http://localhost:8001/api/artifacts/artifact-1/content',
    )

    rerender(
      <ArtifactPreview
        artifact={artifact({
          artifact_kind: 'audio',
          extension: 'mp3',
          mime_type: 'audio/mpeg',
          preview_url: '/api/artifacts/artifact-1/content',
          download_url: '/api/artifacts/artifact-1/download',
        })}
      />,
    )
    expect(container.querySelector('audio')?.getAttribute('src')).toBe(
      'http://localhost:8001/api/artifacts/artifact-1/content',
    )
  })
})
