import type { ThreadMessage } from '@assistant-ui/react'
import { describe, expect, it, vi } from 'vitest'
import {
  findChatMessageLink,
  storedChatMessageId,
  wireChatMessageId,
} from '../chat-message-identity'
import { agUiFeedbackAdapter } from '../chat-adapters'
import type { WireChatSnapshot } from '../chat-history-types'

const id = '371fc2c0-6d30-4a4d-8f81-70c327af9d93'
const message: ThreadMessage = {
  id: 'local',
  role: 'assistant',
  content: [],
  createdAt: new Date(),
  status: { type: 'complete', reason: 'stop' },
  metadata: {
    custom: { agui: { messageId: 'wire' } },
    unstable_state: null,
    unstable_annotations: [],
    unstable_data: [],
    steps: [],
  },
}
const link = {
  messageId: 'wire',
  uiMessageId: id,
  runId: null,
  physicalRunId: null,
  parentMessageId: null,
  checkpointId: null,
  parentCheckpointId: null,
  createdAt: undefined,
  subagentRunId: undefined,
}

describe('AG-UI feedback targets', () => {
  it('submits the database UUID for an SDK-local ID using a freshly scoped snapshot', async () => {
    const submit = vi.fn()
    const readSnapshot = vi.fn(async () => ({ messageLinks: [link] }))
    const feedback = agUiFeedbackAdapter({ submit }, readSnapshot)
    await feedback?.submit({ message, type: 'positive', comment: 'helpful' })
    expect(readSnapshot).toHaveBeenCalledOnce()
    expect(submit).toHaveBeenCalledExactlyOnceWith({
      message: { ...message, id },
      type: 'positive',
      comment: 'helpful',
    })
  })
  it('ignores a child with the same message ID and declines ambiguous root rows', () => {
    const child = { ...link, subagentRunId: 'child', uiMessageId: 'child-ui' }
    expect(findChatMessageLink(message, [child, link])).toEqual(link)
    expect(findChatMessageLink(message, [child])).toBeUndefined()
    expect(
      findChatMessageLink(message, [link, { ...link, uiMessageId: 'sibling-ui' }]),
    ).toBeUndefined()
    const hydrated = {
      ...message,
      metadata: {
        ...message.metadata,
        custom: { publicMessageId: id, agui: { messageId: 'wire' } },
      },
    }
    expect(findChatMessageLink(hydrated, [link, { ...link, uiMessageId: 'sibling-ui' }])).toEqual(
      link,
    )
    expect(storedChatMessageId(hydrated)).toBe(id)
    expect(wireChatMessageId(hydrated)).toBe('wire')
  })

  it('drops unmapped and non-UUID targets', async () => {
    const submit = vi.fn()
    const log = vi.spyOn(console, 'error').mockImplementation(() => {})
    const readSnapshot = vi.fn<() => Promise<Pick<WireChatSnapshot, 'messageLinks'>>>()
    readSnapshot.mockResolvedValueOnce({ messageLinks: [] })
    readSnapshot.mockResolvedValueOnce({
      messageLinks: [{ ...link, uiMessageId: 'provider-only' }],
    })
    const feedback = agUiFeedbackAdapter({ submit }, readSnapshot)
    await feedback?.submit({ message, type: 'negative' })
    await feedback?.submit({ message, type: 'positive' })
    expect(submit).not.toHaveBeenCalled()
    log.mockRestore()
  })
})
