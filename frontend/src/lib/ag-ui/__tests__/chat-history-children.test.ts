import type { Message, ToolCall } from '@ag-ui/client'
import {
  fromThreadMessageLike,
  type ThreadAssistantMessage,
  type ThreadMessage,
} from '@assistant-ui/react'
import { describe, expect, it } from 'vitest'
import {
  indexAgUiSubagents,
  nestAgUiHistoryChildren,
  type AgUiHistoryChild,
} from '../chat-history-children'

function parent(messageId: string, ...callIds: string[]): ThreadAssistantMessage {
  const message = fromThreadMessageLike(
    {
      role: 'assistant',
      id: messageId,
      content: callIds.map((toolCallId) => ({
        type: 'tool-call',
        toolCallId,
        toolName: 'task',
        args: {},
      })),
    },
    messageId,
    { type: 'complete', reason: 'stop' },
  )
  if (message.role !== 'assistant') throw new Error('Expected assistant fixture')
  return message
}

function child(
  subagentRunId: string,
  parentToolCallId: string,
  extra: Partial<AgUiHistoryChild> = {},
): AgUiHistoryChild {
  return { subagentRunId, parentToolCallId, name: 'researcher', status: 'completed', ...extra }
}

function record(subagentRunId: string, message: Message) {
  return { subagentRunId, message }
}

function tool(id: string, name: string): ToolCall {
  return { id, type: 'function', function: { name, arguments: '{}' } }
}

function childrenUnder(
  message: ThreadMessage | undefined,
  callId: string,
): readonly ThreadMessage[] {
  return (
    message?.content.flatMap((part) =>
      part.type === 'tool-call' && part.toolCallId === callId ? (part.messages ?? []) : [],
    ) ?? []
  )
}

describe('restoring public subagent history', () => {
  it('uses the originating parent and isolates repeated message, tool, and result IDs across two children', () => {
    const roots = [parent('turn-a', 'task'), parent('turn-b', 'task')]
    const children = ['a', 'b'].map((id) => child(id, 'task', { parentMessageId: `turn-${id}` }))
    const records = [
      ...['b', 'a'].map((id) =>
        record(id, {
          role: 'assistant',
          id: 'same-message',
          content: `Finding ${id}`,
          toolCalls: [tool('same-call', 'search')],
        }),
      ),
      ...['a', 'b'].map((id) =>
        record(id, {
          role: 'tool',
          id: 'same-result',
          toolCallId: 'same-call',
          content: `Result ${id}`,
        }),
      ),
    ]
    const messages = nestAgUiHistoryChildren(roots, records, children)
    expect(messages.map(({ id }) => id)).toEqual(['turn-a', 'turn-b'])
    const index = indexAgUiSubagents(messages)
    expect(index.size).toBe(2)
    for (const id of ['a', 'b']) {
      const owner = messages.find((message) => message.id === `turn-${id}`)
      expect(childrenUnder(owner, 'task').map((nested) => nested.id)).toEqual([id])
      expect(index.get(id)).toMatchObject({
        parentMessageId: `turn-${id}`,
        subagentRunId: id,
        transcript: [expect.objectContaining({ text: `Finding ${id}` })],
        tools: [{ id: 'same-call', name: 'search', result: `Result ${id}` }],
      })
    }
  })

  it('attaches a grandchild only to the named assistant message in the named parent run', () => {
    const messages = nestAgUiHistoryChildren(
      [parent('root', 'delegate-left', 'delegate-right')],
      [
        ...['left', 'right'].map((id) =>
          record(id, {
            role: 'assistant',
            id: 'delegate-again',
            toolCalls: [tool('reused', 'task')],
          }),
        ),
        record('grandchild', {
          role: 'assistant',
          id: 'grandchild-findings',
          content: 'Inner result',
        }),
      ],
      [
        child('left', 'delegate-left'),
        child('right', 'delegate-right'),
        child('grandchild', 'reused', {
          parentSubagentRunId: 'right',
          parentMessageId: 'delegate-again',
        }),
      ],
    )
    const index = indexAgUiSubagents(messages)
    expect(index.size).toBe(3)
    const left = childrenUnder(messages[0], 'delegate-left')[0]
    const right = childrenUnder(messages[0], 'delegate-right')[0]
    expect(childrenUnder(left, 'reused')).toHaveLength(0)
    expect(childrenUnder(right, 'reused').map(({ id }) => id)).toEqual(['grandchild'])
    expect(index.get('grandchild')).toMatchObject({
      depth: 2,
      transcript: [expect.objectContaining({ text: 'Inner result' })],
    })
    expect(JSON.stringify(index.get('left'))).not.toContain('Inner result')
    expect(JSON.stringify(index.get('right'))).not.toContain('Inner result')
  })

  it('uses a verified wire alias to select the parent while indexing its visible runtime ID', () => {
    const roots = ['old', 'new'].map((turn) => {
      const message = parent(`database-${turn}`, 'reused-call')
      return {
        ...message,
        metadata: { ...message.metadata, custom: { agui: { messageId: `wire-${turn}` } } },
      }
    })
    const messages = nestAgUiHistoryChildren(
      roots,
      [],
      [child('worker', 'reused-call', { parentMessageId: 'wire-new' })],
    )
    expect(childrenUnder(messages[0], 'reused-call')).toEqual([])
    expect(childrenUnder(messages[1], 'reused-call').map(({ id }) => id)).toEqual(['worker'])
    expect(indexAgUiSubagents(messages).get('worker')?.parentMessageId).toBe('database-new')
  })

  it('keeps allowlisted, validated activity cards in the authorized child scope', () => {
    const note = (
      id: string,
      text: string,
      type = 'demo_note',
      subagentRunId = 'left',
    ): Message => ({
      id,
      role: 'activity',
      activityType: 'moldy.ui_data',
      subagentRunId,
      content: { type, props: { text, ignored: 'unlisted property' } },
    })
    const records = [
      record('left', { role: 'assistant', id: 'm-left', content: 'Findings' }),
      record('right', { role: 'assistant', id: 'm-right', content: 'Other findings' }),
      record('left', note('card', 'Approved note')),
      record('left', note('unknown', 'unrecognized card', 'unknown_card')),
      record('left', note('crossed', 'unrelated owner', 'demo_note', 'right')),
    ]
    const messages = nestAgUiHistoryChildren([parent('root', 'a', 'b')], records, [
      child('left', 'a'),
      child('right', 'b'),
    ])
    const index = indexAgUiSubagents(messages)
    expect(index.get('left')?.dataCards).toMatchObject([
      { type: 'demo_note', props: { text: 'Approved note' } },
    ])
    expect(index.get('right')?.dataCards ?? []).toEqual([])
    expect(JSON.stringify([...index.values()])).not.toMatch(
      /unlisted property|unrecognized card|unrelated owner/,
    )
  })

  it('skips an ambiguous parent, a wrong parentMessageId, and unreachable or circular ancestry', () => {
    const roots = [parent('one', 'repeated'), parent('two', 'repeated', 'unique')]
    const children = [
      child('ambiguous', 'repeated'),
      child('wrong-parent', 'unique', { parentMessageId: 'one' }),
      child('orphan', 'missing'),
      child('cycle-1', 'a', { parentSubagentRunId: 'cycle-2' }),
      child('cycle-2', 'b', { parentSubagentRunId: 'cycle-1' }),
    ]
    const records = children.map(({ subagentRunId }) =>
      record(subagentRunId, {
        role: 'assistant',
        id: `message-${subagentRunId}`,
        content: 'Unattributed message',
      }),
    )
    const messages = nestAgUiHistoryChildren(roots, records, children)
    expect(indexAgUiSubagents(messages).size).toBe(0)
    expect(JSON.stringify(messages)).not.toContain('Unattributed message')
  })

  it('exposes public text while keeping reasoning, system instructions, and structured tool output out of details', () => {
    const records = [
      record('worker', {
        role: 'reasoning',
        id: 'r',
        content: 'private reasoning',
        encryptedValue: 'opaque token',
      }),
      record('worker', { role: 'system', id: 's', content: 'private instructions' }),
      record('worker', {
        role: 'assistant',
        id: 'm',
        content: 'Shareable finding',
        toolCalls: [tool('search', 'lookup')],
      }),
      record('worker', {
        role: 'tool',
        id: 't',
        toolCallId: 'search',
        content: '{"internal":"private blob"}',
      }),
    ]
    const index = indexAgUiSubagents(
      nestAgUiHistoryChildren([parent('root', 'delegate')], records, [child('worker', 'delegate')]),
    )
    const snapshot = index.get('worker')
    expect(snapshot).toMatchObject({
      transcript: [expect.objectContaining({ text: 'Shareable finding' })],
      tools: [expect.objectContaining({ id: 'search', name: 'lookup' })],
    })
    expect(snapshot?.tools[0]?.result).toBeUndefined()
    expect(JSON.stringify(snapshot)).not.toMatch(
      /private reasoning|opaque token|private instructions|private blob/,
    )
  })

  it.each([
    ['cancelled', { type: 'incomplete', reason: 'cancelled' }],
    ['interrupted', { type: 'requires-action', reason: 'interrupt' }],
  ] as const)('restores %s even when the child emitted no text', (outcome, status) => {
    const messages = nestAgUiHistoryChildren(
      [parent('root', 'task')],
      [],
      [child('worker', 'task', { status: outcome })],
    )
    const part = messages[0]?.content[0]
    expect(part?.type === 'tool-call' && part.messages?.[0]).toMatchObject({ id: 'worker', status })
    expect(indexAgUiSubagents(messages).get('worker')?.outcome).toBe(outcome)
  })
})
