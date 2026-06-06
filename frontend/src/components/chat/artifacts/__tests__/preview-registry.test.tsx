import { describe, expect, it } from 'vitest'
import { getArtifactPreviewProvider, registerArtifactPreviewProvider } from '../preview-registry'
import type { ArtifactSummary } from '@/lib/types'

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

describe('getArtifactPreviewProvider', () => {
  it('selects mermaid before markdown for mermaid extensions', () => {
    const provider = getArtifactPreviewProvider(artifact({ extension: 'mmd' }))

    expect(provider.id).toBe('mermaid')
  })

  it('selects image provider for image artifacts', () => {
    const provider = getArtifactPreviewProvider(
      artifact({ artifact_kind: 'image', mime_type: 'image/png', extension: 'png' }),
    )

    expect(provider.id).toBe('image')
  })

  it('selects table data provider for csv and tsv artifacts', () => {
    expect(
      getArtifactPreviewProvider(
        artifact({ artifact_kind: 'data', mime_type: 'text/csv', extension: 'csv' }),
      ).id,
    ).toBe('table-data')
    expect(
      getArtifactPreviewProvider(
        artifact({
          artifact_kind: 'data',
          mime_type: 'text/tab-separated-values',
          extension: 'tsv',
        }),
      ).id,
    ).toBe('table-data')
  })

  it('selects json data provider before generic code preview', () => {
    const provider = getArtifactPreviewProvider(
      artifact({ artifact_kind: 'code', mime_type: 'application/json', extension: 'json' }),
    )

    expect(provider.id).toBe('json-data')
  })

  it('selects structured data provider before generic code preview', () => {
    expect(
      getArtifactPreviewProvider(
        artifact({ artifact_kind: 'code', mime_type: 'application/yaml', extension: 'yaml' }),
      ).id,
    ).toBe('structured-data')
    expect(
      getArtifactPreviewProvider(
        artifact({ artifact_kind: 'code', mime_type: 'application/toml', extension: 'toml' }),
      ).id,
    ).toBe('structured-data')
  })

  it('allows preview add-ons to register by manifest', () => {
    registerArtifactPreviewProvider({
      id: 'custom-addon',
      priority: 88,
      requiresText: true,
      extensions: ['custompreview'],
      render: () => null,
    })

    const provider = getArtifactPreviewProvider(
      artifact({
        artifact_kind: 'other',
        mime_type: 'application/octet-stream',
        extension: 'custompreview',
      }),
    )

    expect(provider.id).toBe('custom-addon')
  })

  it('falls back for unsupported files', () => {
    const provider = getArtifactPreviewProvider(
      artifact({ artifact_kind: 'cad', mime_type: 'application/octet-stream', extension: 'dwg' }),
    )

    expect(provider.id).toBe('fallback')
  })
})
