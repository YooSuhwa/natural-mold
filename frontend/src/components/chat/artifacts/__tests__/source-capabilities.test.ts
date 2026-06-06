import { describe, expect, it } from 'vitest'
import type { ArtifactSummary } from '@/lib/types'
import { canShowArtifactSource } from '../source-capabilities'

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

describe('canShowArtifactSource', () => {
  it('allows source mode for text-like data files', () => {
    expect(
      canShowArtifactSource(
        artifact({ artifact_kind: 'data', mime_type: 'text/csv', extension: 'csv' }),
      ),
    ).toBe(true)
    expect(
      canShowArtifactSource(
        artifact({ artifact_kind: 'code', mime_type: 'application/json', extension: 'json' }),
      ),
    ).toBe(true)
  })

  it('does not allow source mode for binary spreadsheet files', () => {
    expect(
      canShowArtifactSource(
        artifact({
          artifact_kind: 'data',
          mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
          extension: 'xlsx',
        }),
      ),
    ).toBe(false)
    expect(
      canShowArtifactSource(
        artifact({
          artifact_kind: 'data',
          mime_type: 'application/vnd.ms-excel',
          extension: 'xls',
        }),
      ),
    ).toBe(false)
  })
})
