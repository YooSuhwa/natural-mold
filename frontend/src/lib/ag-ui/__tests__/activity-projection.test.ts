import { describe, expect, it } from 'vitest'
import type { ThreadMessage } from '@assistant-ui/react'
import type { Message } from '@/lib/types'
import type { AgUiActivitySnapshot, AgUiMessageLink } from '../chat-history-types'
import {
  applyMoldyActivitiesToHistory,
  indexMoldyMessageActivities,
  mergeActivityFiles,
  messageActivityPresentation,
  toMoldyRunActivities,
} from '../activity-projection'
import { fileActivitySummary } from '../activity-values'

const usage = {
  prompt_tokens: 21,
  completion_tokens: 3,
  cache_creation_tokens: 0,
  cache_read_tokens: 5,
}
const historyId = 'history_1234567890abcdef12345678'
function activity(
  activityType: string,
  content: Record<string, unknown>,
  extra: Partial<AgUiActivitySnapshot> = {},
): AgUiActivitySnapshot {
  return { type: 'ACTIVITY_SNAPSHOT', messageId: 'entity', activityType, content, ...extra }
}
function link(messageId: string, uiMessageId: string, physicalRunId: string): AgUiMessageLink {
  return {
    messageId,
    uiMessageId,
    physicalRunId,
    runId: 'run',
    checkpointId: null,
    parentMessageId: null,
    parentCheckpointId: null,
  }
}
function assistant(id: string, custom: Record<string, unknown> = {}): ThreadMessage {
  return {
    role: 'assistant',
    id,
    content: [{ type: 'text', text: 'answer' }],
    createdAt: new Date(0),
    status: { type: 'complete', reason: 'stop' },
    metadata: {
      unstable_state: null,
      unstable_annotations: [],
      unstable_data: [],
      steps: [],
      custom: { kept: true, ...custom },
    },
  }
}

describe('AG-UI message activity ownership', () => {
  it('binds a completed compaction to the proved message and keeps duplicate wire IDs across runs separate', () => {
    const links = [link('model', 'row-A', 'old'), link('model', 'row-B', 'now')]
    const events = [
      activity(
        'moldy.compaction',
        { state: 'done', history_id: historyId, cutoff_index: 5 },
        { parentMessageId: 'model', physicalRunId: 'now' },
      ),
    ]
    const index = indexMoldyMessageActivities(events, links)
    expect(
      messageActivityPresentation(index, assistant('model', { publicMessageId: 'row-A' })),
    ).toBeUndefined()
    expect(messageActivityPresentation(index, assistant('model'))).toBeUndefined()
    expect(
      messageActivityPresentation(index, assistant('model', { publicMessageId: 'row-B' })),
    ).toMatchObject({
      compaction: { historyId, cutoffIndex: 5 },
      compactionMessageId: 'model',
    })
  })

  it('keeps child metrics out of the parent, even when their wire message IDs coincide', () => {
    const source = activity(
      'moldy.usage',
      { ...usage, assistant_msg_id: 'shared' },
      { subagentRunId: 'child', parentMessageId: 'shared' },
    )
    const index = indexMoldyMessageActivities([source], [])
    expect(messageActivityPresentation(index, assistant('shared'))).toBeUndefined()
    expect(messageActivityPresentation(index, assistant('child'))).toBeUndefined()
    expect(
      messageActivityPresentation(index, assistant('child', { agui: { name: 'worker' } }))?.usage,
    ).toEqual(usage)
  })

  it('restores authoritative REST usage via the SDK identity and keeps native metadata', () => {
    const row: Message = {
      id: 'db',
      runtime_message_id: 'model',
      conversation_id: 'thread',
      role: 'assistant',
      content: 'answer',
      tool_calls: null,
      tool_call_id: null,
      created_at: '',
      usage,
    }
    const [message] = applyMoldyActivitiesToHistory(
      [assistant('model')],
      [],
      [row],
      [link('model', 'db', 'run')],
    )
    expect(message?.metadata.custom).toMatchObject({ kept: true, usage })
  })

  it('does not infer a bubble from the activity entity ID or disclose an unproved archive reference', () => {
    const hidden = activity('moldy.compaction', { state: 'done', history_id: 'path/to/archive' })
    const index = indexMoldyMessageActivities([hidden], [])
    expect(messageActivityPresentation(index, assistant(hidden.messageId))).toBeUndefined()
    const explicit = indexMoldyMessageActivities([{ ...hidden, parentMessageId: 'bound' }], [])
    expect(messageActivityPresentation(explicit, assistant('bound'))?.compaction).toEqual({})
  })

  it('drops a cached file until the current viewer can read its exact version', () => {
    const id = '28637d28-cb49-4660-a3f2-c2ba01dc0207'
    const version = 'd94a90e5-d2ba-4a53-ac22-1240c586bc3b'
    const url = '/api/conversations/thread/artifacts/' + id
    const saved = fileActivitySummary(
      {
        id,
        version_id: version,
        version_number: 1,
        op: 'created',
        conversation_id: 'thread',
        assistant_msg_id: 'run',
        run_id: 'run',
        path: 'notes.md',
        display_name: 'notes.md',
        mime_type: 'text/markdown',
        artifact_kind: 'markdown',
        size_bytes: 2,
        sha256: 'a'.repeat(64),
        status: 'ready',
        is_favorite: false,
        preview_count: 0,
        download_count: 0,
        created_at: '2026-11-22',
        updated_at: '2026-11-22',
        url,
        preview_url: url + '/content',
        download_url: url + '/download',
      },
      'thread',
    )
    expect(saved).toBeTruthy()
    if (!saved) throw new Error('invalid test fixture')
    const pointer = activity(
      'moldy.file_event',
      { id, version_id: version, op: 'created' },
      { parentMessageId: 'model', physicalRunId: 'run' },
    )
    const pending = messageActivityPresentation(
      indexMoldyMessageActivities([pointer], []),
      assistant('model'),
    )
    expect(mergeActivityFiles([saved], pending?.fileEvents)).toEqual([])
    expect(mergeActivityFiles([], [saved])).toMatchObject([{ id, version_id: version }])
  })

  it('updates an existing activity through running/done and excludes another physical run', () => {
    const current = activity('moldy.compaction', { state: 'running' }, { physicalRunId: 'run' })
    const done = { ...current, content: { state: 'done' } }
    const old = { ...current, messageId: 'other', physicalRunId: 'old' }
    const unknown = activity('moldy.other', { value: 'ignored' })
    expect(toMoldyRunActivities([current, done, old, unknown], 'run')).toMatchObject([
      { kind: 'compaction', status: 'complete', runId: 'run' },
    ])
  })
})
