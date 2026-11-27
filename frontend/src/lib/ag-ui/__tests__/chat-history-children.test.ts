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

describe('public tool ownership for child data cards', () => {
  const issuer = `message-${'a'.repeat(64)}`
  const otherIssuer = `message-${'b'.repeat(64)}`
  const leftCall = `tool-${'1'.repeat(64)}`
  const rightCall = `tool-${'2'.repeat(64)}`
  const grandchildCall = `tool-${'3'.repeat(64)}`
  const duplicateCall = `tool-${'4'.repeat(64)}`
  const rootCall = `tool-${'5'.repeat(64)}`
  const repeatedInOneMessage = `tool-${'6'.repeat(64)}`
  const raw = 'provider-call-19'
  const brief = 'Review the same report'
  const claim = (publicId: string, messageId = issuer) => ({
    tool_call_id: raw,
    public_tool_call_id: publicId,
    public_message_id: messageId,
  })
  const cases: readonly [string, Record<string, string>][] = [
    ['verified', claim(leftCall)],
    ['plain message', { public_message_id: issuer }],
    ['raw only', { tool_call_id: leftCall, public_message_id: issuer }],
    ['raw claimed as public', claim(raw)],
    ['unissued', claim(`tool-${'0'.repeat(64)}`)],
    ['wrong issuing message', claim(leftCall, otherIssuer)],
    ['different child', claim(rightCall)],
    ['nested child', claim(grandchildCall)],
    ['root tool', claim(rootCall)],
    ['duplicate in same child', claim(duplicateCall)],
    ['duplicate in same message', claim(repeatedInOneMessage)],
  ]
  const payload = (text: string, binding: Record<string, string>) => ({
    type: 'demo_note',
    props: { text },
    ...binding,
  })

  function restored() {
    const ownCalls = (id: string, messageId: string, calls: string[]) =>
      record(id, {
        role: 'assistant',
        id: messageId,
        subagentRunId: id,
        content: brief,
        toolCalls: calls.map((callId) => tool(callId, 'lookup')),
      })
    const records = [
      ownCalls('left', otherIssuer, [duplicateCall]),
      ownCalls('left', issuer, [
        leftCall,
        'spawn-grandchild',
        duplicateCall,
        repeatedInOneMessage,
        repeatedInOneMessage,
      ]),
      ...cases.map(([text, binding]) =>
        record('left', {
          role: 'activity',
          id: text,
          activityType: 'moldy.ui_data',
          subagentRunId: 'left',
          content: payload(text, binding),
        }),
      ),
      ownCalls('right', issuer, [rightCall]),
      ownCalls('grandchild', issuer, [grandchildCall]),
    ]
    return nestAgUiHistoryChildren(
      [parent('root', 'spawn-left', 'spawn-right', rootCall)],
      records,
      [
        child('left', 'spawn-left', { description: brief }),
        child('right', 'spawn-right', { description: brief }),
        child('grandchild', 'spawn-grandchild', {
          parentSubagentRunId: 'left',
          parentMessageId: issuer,
          description: brief,
        }),
      ],
    )
  }

  function liveWrapper() {
    const call = (id: string, parentId: string, nested?: ThreadMessage) => ({
      type: 'tool-call' as const,
      toolCallId: id,
      toolName: 'lookup',
      args: {},
      parentId,
      ...(nested && { messages: [nested] }),
    })
    type Content = Parameters<typeof fromThreadMessageLike>[0]['content']
    function message(id: string, content: Content) {
      return fromThreadMessageLike(
        {
          id,
          role: 'assistant',
          content,
          metadata: { custom: { agui: { name: 'researcher', description: brief } } },
        },
        id,
        { type: 'complete', reason: 'stop' },
      )
    }
    const grandchild = message('grandchild', [call(grandchildCall, issuer)])
    const left = message('left', [
      call(duplicateCall, otherIssuer),
      call(leftCall, issuer),
      call(duplicateCall, issuer),
      call('spawn-grandchild', issuer, grandchild),
      call(repeatedInOneMessage, issuer),
      call(repeatedInOneMessage, issuer),
      ...cases.map(([text, binding]) => ({
        type: 'data' as const,
        name: 'agui-activity/moldy.ui_data',
        data: payload(text, binding),
      })),
    ])
    const right = message('right', [call(rightCall, issuer)])
    return [
      message('root', [
        call(rootCall, issuer),
        call('spawn-left', 'root', left),
        call('spawn-right', 'root', right),
      ]),
    ]
  }

  it.each([
    ['live SDK child', liveWrapper],
    ['restored child', restored],
  ] as const)(
    '%s admits only the verified direct public call and plain message cards',
    (_path, build) => {
      const index = indexAgUiSubagents(build())
      expect([...index.keys()].sort()).toEqual(['grandchild', 'left', 'right'])
      expect(index.get('right')?.dataCards ?? []).toEqual([])
      expect(index.get('grandchild')?.dataCards ?? []).toEqual([])
      expect(index.get('left')?.dataCards?.map((card) => card.props.text)).toEqual([
        'verified',
        'plain message',
      ])
    },
  )
})

describe('public task descriptions for legacy child runs', () => {
  const callId = `tool-${'1'.repeat(64)}`
  const wireId = `message-${'2'.repeat(64)}`
  const brief = 'Summarize the public report.'

  function sdkChild(id: string, meta: { parentMessageId?: string; description?: string } = {}) {
    return fromThreadMessageLike(
      {
        id,
        role: 'assistant',
        content: [],
        metadata: { custom: { agui: { name: 'Researcher', ...meta } } },
      },
      id,
      { type: 'running' },
    )
  }

  function rootCall({
    name = 'task',
    description = brief,
    issuer = wireId,
    nested = [],
  }: {
    name?: string
    description?: string | { privateValue: string }
    issuer?: string | null
    nested?: ThreadMessage[]
  } = {}): ThreadAssistantMessage {
    const result = fromThreadMessageLike(
      {
        id: 'database-parent',
        role: 'assistant',
        metadata: { custom: { agui: { messageId: wireId } } },
        content: [
          {
            type: 'tool-call',
            toolCallId: callId,
            toolName: name,
            args: { description },
            ...(issuer !== null && { parentId: issuer }),
            messages: nested,
          },
        ],
      },
      'database-parent',
      { type: 'running' },
    )
    if (result.role !== 'assistant') throw new Error('Expected parent fixture')
    return result
  }

  it('recovers a live brief from the unique root task using its wire issuer alias', () => {
    const root = rootCall({ nested: [sdkChild('worker')] })
    expect(indexAgUiSubagents([root]).get('worker')).toMatchObject({
      parentMessageId: 'database-parent',
      taskInput: brief,
      outcome: 'running',
    })
  })

  it('uses the verified parent recorded by history even when the root part lacks a parentId', () => {
    const roots = nestAgUiHistoryChildren(
      [rootCall({ issuer: null })],
      [],
      [child('worker', callId, { parentMessageId: wireId })],
    )
    expect(indexAgUiSubagents(roots).get('worker')?.taskInput).toBe(brief)
  })

  it('prefers the explicit public description from the child event', () => {
    const nested = sdkChild('worker', { description: 'Brief sent by the child event' })
    expect(indexAgUiSubagents([rootCall({ nested: [nested] })]).get('worker')?.taskInput).toBe(
      'Brief sent by the child event',
    )
  })

  it('withholds the brief for duplicate root calls, multiple children, or a conflicting issuer', () => {
    const worker = sdkChild('worker')
    const duplicate = { ...rootCall({ description: 'Other turn' }), id: 'other-turn' }
    const cases = [
      [rootCall({ nested: [worker] }), duplicate],
      [rootCall({ nested: [worker, sdkChild('sibling')] })],
      [rootCall({ issuer: 'wrong-issuer', nested: [worker] })],
      [rootCall({ nested: [sdkChild('worker', { parentMessageId: 'wrong-issuer' })] })],
      [rootCall({ issuer: null, nested: [worker] })],
    ]
    for (const roots of cases) {
      const index = indexAgUiSubagents(roots)
      expect(index.get('worker')?.taskInput).toBeUndefined()
      expect(index.get('sibling')?.taskInput).toBeUndefined()
    }
  })

  it('ignores unrelated tool descriptions and structured values', () => {
    for (const args of [{ name: 'lookup' }, { description: { privateValue: 'hidden' } }]) {
      const root = rootCall({ ...args, nested: [sdkChild('worker')] })
      expect(indexAgUiSubagents([root]).get('worker')?.taskInput).toBeUndefined()
    }
  })

  it('does not borrow a brief from a child-owned task', () => {
    const inner = rootCall({
      description: 'Nested private input',
      nested: [sdkChild('grandchild')],
    })
    const nested = {
      ...inner,
      id: 'child',
      metadata: { ...inner.metadata, custom: { agui: { name: 'Child', messageId: wireId } } },
    }
    const index = indexAgUiSubagents([rootCall({ nested: [nested] })])
    expect(index.get('child')?.taskInput).toBe(brief)
    expect(index.get('grandchild')?.depth).toBe(2)
    expect(index.get('grandchild')?.taskInput).toBeUndefined()
  })
})
