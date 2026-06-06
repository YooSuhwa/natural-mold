import { describe, expect, it } from 'vitest'
import type { ArtifactSummary } from '@/lib/types'
import { selectMessageArtifactsFromMetadata } from '../message-artifact-metadata'

function artifact(): ArtifactSummary {
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
  }
}

describe('selectMessageArtifactsFromMetadata', () => {
  it('returns one stable empty list for messages without artifacts', () => {
    const first = selectMessageArtifactsFromMetadata(undefined)
    const second = selectMessageArtifactsFromMetadata({ custom: {} })
    const third = selectMessageArtifactsFromMetadata({ custom: { artifacts: null } })

    expect(first).toBe(second)
    expect(second).toBe(third)
    expect(first).toEqual([])
  })

  it('returns the existing artifact array when one is present', () => {
    const artifacts = [artifact()]

    expect(selectMessageArtifactsFromMetadata({ custom: { artifacts } })).toBe(artifacts)
  })
})
