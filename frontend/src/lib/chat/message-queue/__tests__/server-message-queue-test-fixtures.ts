import type { AppendMessage } from '@assistant-ui/react'

import type {
  ConversationRunInput,
  ConversationRunInputList,
} from '@/lib/api/conversation-run-inputs'

export const message = (text: string, attachmentIds: readonly string[] = []): AppendMessage => ({
  role: 'user',
  content: [{ type: 'text', text }],
  attachments: attachmentIds.map((id) => ({
    id,
    type: 'document',
    name: `${id}.txt`,
    contentType: 'text/plain',
    content: [],
    status: { type: 'complete' },
  })),
  createdAt: new Date('2026-09-06T00:00:00Z'),
  parentId: null,
  sourceId: null,
  runConfig: {},
  metadata: { custom: { context: [{ kind: 'artifact', id: 'artifact-1' }] } },
})

export const queuedInput = (
  overrides: Partial<ConversationRunInput> = {},
): ConversationRunInput => ({
  id: 'input-1',
  conversation_id: 'conversation-1',
  run_id: null,
  client_request_id: 'request-1',
  source: 'chat',
  status: 'pending',
  priority: 0,
  position: 1,
  revision: 1,
  input_payload: { messages: [{ role: 'user', content: 'queued' }] },
  resource_context: [],
  attachment_ids: ['attachment-1'],
  checkpoint_id: null,
  claimed_at: null,
  created_at: '2026-09-06T00:00:00',
  updated_at: '2026-09-06T00:00:00',
  ...overrides,
})

export const queueList = (
  items: readonly ConversationRunInput[],
  queuePaused = false,
): ConversationRunInputList => ({ queue_paused: queuePaused, items })
