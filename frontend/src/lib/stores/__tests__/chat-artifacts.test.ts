import { describe, expect, it } from 'vitest'
import { createStore } from 'jotai'
import {
  chatArtifactsAtom,
  selectChatArtifactAtom,
  setConversationArtifactsAtom,
  upsertArtifactList,
  upsertChatArtifactAtom,
} from '../chat-artifacts'
import type { ArtifactSummary, FileEventPayload } from '@/lib/types'

function artifact(overrides: Partial<FileEventPayload>): FileEventPayload {
  return {
    op: 'created',
    id: 'artifact-1',
    agent_id: 'agent-1',
    conversation_id: 'conversation-1',
    assistant_msg_id: 'run-1',
    run_id: 'run-1',
    tool_call_id: 'call-1',
    source_tool_name: 'execute_in_skill',
    path: 'report.md',
    display_name: 'report.md',
    mime_type: 'text/markdown',
    extension: 'md',
    artifact_kind: 'markdown',
    size_bytes: 12,
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

describe('upsertArtifactList', () => {
  it('adds new file events', () => {
    const items = upsertArtifactList([], artifact({}))

    expect(items).toHaveLength(1)
    expect(items[0]?.path).toBe('report.md')
  })

  it('updates existing artifacts by id', () => {
    const first = upsertArtifactList([], artifact({}))
    const updated = upsertArtifactList(
      first,
      artifact({ op: 'updated', size_bytes: 24, version_number: 2, version_id: 'version-2' }),
    )

    expect(updated).toHaveLength(1)
    expect(updated[0]?.size_bytes).toBe(24)
    expect(updated[0]?.version_number).toBe(2)
  })

  it('removes deleted artifacts', () => {
    const first: ArtifactSummary[] = upsertArtifactList([], artifact({}))
    const deleted = upsertArtifactList(first, artifact({ op: 'deleted' }))

    expect(deleted).toHaveLength(0)
  })
})

describe('setConversationArtifactsAtom', () => {
  it('keeps the selected artifact when a refetch returns the same item', () => {
    const store = createStore()
    const report = artifact({ id: 'report', path: 'report.md', display_name: 'report.md' })
    const code = artifact({
      id: 'code',
      path: 'code/example.py',
      display_name: 'example.py',
      artifact_kind: 'code',
      extension: 'py',
      mime_type: 'text/x-python',
    })

    store.set(setConversationArtifactsAtom, {
      conversationId: 'conversation-1',
      items: [report, code],
    })
    store.set(selectChatArtifactAtom, {
      conversationId: 'conversation-1',
      artifactId: 'code',
    })
    store.set(setConversationArtifactsAtom, {
      conversationId: 'conversation-1',
      items: [{ ...report, preview_count: 1 }, { ...code, preview_count: 1 }],
    })

    expect(store.get(chatArtifactsAtom)['conversation-1']?.selectedArtifactId).toBe('code')
  })
})

describe('upsertChatArtifactAtom', () => {
  it('keeps the current selection when a different artifact is deleted', () => {
    const store = createStore()
    const report = artifact({ id: 'report', path: 'report.md', display_name: 'report.md' })
    const chart = artifact({
      id: 'chart',
      path: 'chart.csv',
      display_name: 'chart.csv',
      artifact_kind: 'data',
      extension: 'csv',
      mime_type: 'text/csv',
    })

    store.set(setConversationArtifactsAtom, {
      conversationId: 'conversation-1',
      items: [report, chart],
      selectedArtifactId: 'report',
    })
    store.set(upsertChatArtifactAtom, artifact({ op: 'deleted', id: 'chart' }))

    const state = store.get(chatArtifactsAtom)['conversation-1']
    expect(state?.items.map((item) => item.id)).toEqual(['report'])
    expect(state?.selectedArtifactId).toBe('report')
  })

  it('falls back when the selected artifact is deleted', () => {
    const store = createStore()
    const report = artifact({ id: 'report', path: 'report.md', display_name: 'report.md' })
    const chart = artifact({
      id: 'chart',
      path: 'chart.csv',
      display_name: 'chart.csv',
      artifact_kind: 'data',
      extension: 'csv',
      mime_type: 'text/csv',
    })

    store.set(setConversationArtifactsAtom, {
      conversationId: 'conversation-1',
      items: [report, chart],
      selectedArtifactId: 'chart',
    })
    store.set(upsertChatArtifactAtom, artifact({ op: 'deleted', id: 'chart' }))

    const state = store.get(chatArtifactsAtom)['conversation-1']
    expect(state?.items.map((item) => item.id)).toEqual(['report'])
    expect(state?.selectedArtifactId).toBe('report')
  })
})
