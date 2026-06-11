import { describe, expect, it } from 'vitest'
import {
  conversationPagesContainActiveRun,
  isActiveRunStatus,
  isInterruptedRunStatus,
} from './status'
import type { ConversationListEnvelope } from '@/lib/types'

describe('chat run status helpers', () => {
  it('treats queued, running, and canceling as active spinner states', () => {
    expect(isActiveRunStatus('queued')).toBe(true)
    expect(isActiveRunStatus('running')).toBe(true)
    expect(isActiveRunStatus('canceling')).toBe(true)
    expect(isActiveRunStatus('completed')).toBe(false)
    expect(isActiveRunStatus('interrupted')).toBe(false)
  })

  it('treats interrupted as action-required, not active running', () => {
    expect(isInterruptedRunStatus('interrupted')).toBe(true)
    expect(isInterruptedRunStatus('running')).toBe(false)
  })

  it('detects active runs in paginated conversation data', () => {
    const page: ConversationListEnvelope = {
      next_cursor: null,
      has_more: false,
      items: [
        {
          id: 'conversation-1',
          agent_id: 'agent-1',
          title: 'Done',
          is_pinned: false,
          unread_count: 0,
          last_read_at: null,
          last_unread_at: null,
          last_activity_source: 'chat',
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          active_run: null,
        },
        {
          id: 'conversation-2',
          agent_id: 'agent-1',
          title: 'Running',
          is_pinned: false,
          unread_count: 0,
          last_read_at: null,
          last_unread_at: null,
          last_activity_source: 'chat',
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
          active_run: {
            id: 'run-1',
            conversation_id: 'conversation-2',
            agent_id: 'agent-1',
            parent_run_id: null,
            status: 'running',
            source: 'chat',
            worker_instance_id: 'worker-1',
            interrupt_id: null,
            last_event_id: null,
            input_preview: null,
            error_code: null,
            error_message: null,
            cancel_requested_at: null,
            started_at: null,
            heartbeat_at: null,
            completed_at: null,
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-01T00:00:00Z',
          },
        },
      ],
    }

    expect(conversationPagesContainActiveRun([page])).toBe(true)
  })
})
