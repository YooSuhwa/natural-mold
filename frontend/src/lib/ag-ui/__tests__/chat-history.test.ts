import { toAgUiMessages } from '@assistant-ui/react-ag-ui'
import { describe, expect, it, vi } from 'vitest'
import type { Message } from '@/lib/types'
import { createAgUiChatSession } from '../chat-history'
import { parseWireChatSnapshot } from '../chat-history-types'

const threadId = '0866c31b-fcd8-4186-b314-b996d1a0291a'
const physical = '8dc17b7d-c426-45a3-8336-b90e8aec325e'

function envelope(fields: Record<string, unknown> = {}) {
  return {
    threadId,
    protocolVersion: '1.0',
    projectionVersion: '1',
    checkpointId: null,
    activeTipMessageId: null,
    activeRunId: null,
    physicalRunId: null,
    interruptRunId: null,
    physicalInterruptRunId: null,
    cursor: null,
    messages: [],
    messageLinks: [],
    interrupts: [],
    subagents: [],
    ...fields,
  }
}

function link(
  messageId: string,
  parentMessageId: string | null,
  extras: Record<string, unknown> = {},
) {
  return {
    messageId,
    uiMessageId: messageId,
    parentMessageId,
    runId: null,
    physicalRunId: null,
    checkpointId: null,
    parentCheckpointId: null,
    ...extras,
  }
}

function load(fields: Record<string, unknown>, serverMessages: Message[] = []) {
  const readSnapshot = vi.fn(async () => parseWireChatSnapshot(envelope(fields), threadId))
  const session = createAgUiChatSession({
    conversationId: threadId,
    readSnapshot,
    getServerMessages: () => serverMessages,
  })
  return session.history.load()
}

describe('AG-UI chat history', () => {
  it('requires the requested conversation and supported protocol before importing history', () => {
    expect(() =>
      parseWireChatSnapshot(envelope({ threadId: 'another-thread' }), threadId),
    ).toThrow()
    expect(() => parseWireChatSnapshot(envelope({ protocolVersion: '2.0' }), threadId)).toThrow()
  })

  it('keeps the selected branch and its distinct database and wire IDs', async () => {
    const databaseId = '85dfd980-79b7-44b1-a8a7-3dce178b26ae'
    const repo = await load({
      messages: [
        { role: 'user', id: 'question', content: 'Choose a route' },
        { role: 'assistant', id: 'left', content: 'Left' },
        { role: 'assistant', id: 'right', content: 'Right' },
      ],
      messageLinks: [
        link('question', null),
        link('left', 'question', { checkpointId: 'checkpoint-left' }),
        link('right', 'question', {
          uiMessageId: databaseId,
          checkpointId: 'checkpoint-introduced-right',
          parentCheckpointId: 'checkpoint-question',
          siblingCheckpointIds: ['checkpoint-left', 'checkpoint-right'],
        }),
      ],
      activeTipMessageId: 'right',
      checkpointId: 'checkpoint-right',
    })
    expect(repo.headId).toBe('right')
    const chosen = repo.messages.find(({ message }) => message.id === repo.headId)
    expect(chosen).toMatchObject({
      parentId: 'question',
      message: {
        id: 'right',
        metadata: {
          custom: {
            publicMessageId: databaseId,
            branchCheckpointId: 'checkpoint-introduced-right',
            siblingCheckpointIds: ['checkpoint-left', 'checkpoint-right'],
            branchIndex: 1,
            branchTotal: 2,
          },
        },
      },
    })
    expect(repo.messages.find(({ message }) => message.id === 'left')?.parentId).toBe('question')
  })

  it('retains the active user and completed runs, leaving the active assistant prefix to GET replay', async () => {
    const active = { runId: 'public-active', physicalRunId: physical }
    const repo = await load({
      activeRunId: active.runId,
      physicalRunId: physical,
      activeTipMessageId: 'stream-prefix',
      messages: [
        { id: 'question-1', role: 'user', content: 'Earlier' },
        { id: 'answer-1', role: 'assistant', content: 'Earlier answer' },
        { id: 'question-2', role: 'user', content: 'Keep working' },
        { id: 'stream-prefix', role: 'assistant', content: 'Already streamed' },
      ],
      messageLinks: [
        link('question-1', null),
        link('answer-1', 'question-1', { runId: 'previous-run' }),
        link('question-2', 'answer-1', active),
        link('stream-prefix', 'question-2', active),
      ],
    })
    expect(repo.messages.map(({ message }) => message.id)).toEqual([
      'question-1',
      'answer-1',
      'question-2',
    ])
    expect(repo.headId).toBe('question-2')
    expect(repo.unstable_resume).toBe(false)
  })

  it('never imports a private extension into the rendered history or the official outbound transcript', async () => {
    const repo = await load({
      activeTipMessageId: 'answer',
      messages: [
        { id: 'question', role: 'user', content: 'Request' },
        {
          id: 'answer',
          role: 'assistant',
          content: 'Visible',
          rawEvent: { internal: 'private-internal-frame' },
          metadata: { secretHeader: 'private-header' },
          encryptedValue: 'private-provider-payload',
        },
      ],
      messageLinks: [link('question', null), link('answer', 'question')],
    })
    const outbound = toAgUiMessages(repo.messages.map(({ message }) => message))
    const marker = /private-internal-frame|private-header|private-provider-payload/
    expect(JSON.stringify(repo.messages)).not.toMatch(marker)
    expect(JSON.stringify(outbound)).not.toMatch(marker)
    expect(outbound).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 'answer', role: 'assistant', content: 'Visible' }),
      ]),
    )
  })

  it('keeps child messages inside their spawning tool and excludes an unowned child', async () => {
    const child = { subagentRunId: 'child-a' }
    const orphan = { subagentRunId: 'child-absent' }
    const repo = await load({
      activeTipMessageId: 'parent',
      messages: [
        { id: 'question', role: 'user', content: 'Delegate a check' },
        {
          id: 'parent',
          role: 'assistant',
          content: 'Checking',
          toolCalls: [
            { id: 'task-call', type: 'function', function: { name: 'task', arguments: '{}' } },
          ],
        },
        { id: 'child-result', role: 'assistant', content: 'Child evidence', ...child },
        { id: 'other-result', role: 'assistant', content: 'Out of scope evidence', ...orphan },
      ],
      messageLinks: [
        link('question', null),
        link('parent', 'question'),
        link('child-result', 'parent', child),
        link('other-result', 'parent', orphan),
      ],
      subagents: [
        {
          ...child,
          parentRunId: 'parent-run',
          parentMessageId: 'parent',
          parentToolCallId: 'task-call',
          name: 'Researcher',
          status: 'completed',
        },
      ],
    })
    expect(repo.messages.map(({ message }) => message.id)).toEqual(['question', 'parent'])
    const answer = repo.messages.find(({ message }) => message.id === 'parent')?.message
    const part = answer?.content.find((entry) => entry.type === 'tool-call')
    expect(part).toMatchObject({ type: 'tool-call', toolCallId: 'task-call' })
    expect(JSON.stringify(part?.type === 'tool-call' ? part.messages : [])).toContain(
      'Child evidence',
    )
    expect(JSON.stringify(repo.messages)).not.toContain('Out of scope evidence')
  })

  it('joins a tool result to its own branch when a sibling reuses the same call ID', async () => {
    const branches = ['west', 'east']
    const repo = await load({
      activeTipMessageId: 'answer-east',
      messages: [
        { id: 'question', role: 'user', content: 'Read a forecast' },
        ...branches.flatMap((side) => [
          {
            id: 'call-' + side,
            role: 'assistant',
            toolCalls: [
              {
                id: 'forecast',
                type: 'function',
                function: { name: 'read_forecast', arguments: JSON.stringify({ side }) },
              },
            ],
          },
          { id: 'result-' + side, role: 'tool', toolCallId: 'forecast', content: side + ' report' },
          { id: 'answer-' + side, role: 'assistant', content: side + ' answer' },
        ]),
      ],
      messageLinks: [
        link('question', null),
        ...branches.flatMap((side) => [
          link('call-' + side, 'question'),
          link('result-' + side, 'call-' + side),
          link('answer-' + side, 'result-' + side),
        ]),
      ],
    })
    for (const side of branches) {
      const owner = repo.messages.find(({ message }) => message.id === 'call-' + side)
      expect(owner?.message.content).toContainEqual(
        expect.objectContaining({
          type: 'tool-call',
          toolCallId: 'forecast',
          result: side + ' report',
        }),
      )
      expect(repo.messages.find(({ message }) => message.id === 'answer-' + side)?.parentId).toBe(
        'call-' + side,
      )
    }
    expect(repo.headId).toBe('answer-east')
  })

  it('excludes enrichment from an old conversation whose message ID collides', async () => {
    const repo = await load(
      {
        messages: [{ id: 'answer', role: 'assistant', content: 'Current answer' }],
        messageLinks: [link('answer', null)],
        activeTipMessageId: 'answer',
      },
      [
        {
          id: 'answer',
          conversation_id: 'previous-thread',
          role: 'assistant',
          content: 'Different conversation',
          tool_call_id: null,
          tool_calls: null,
          created_at: '2026-11-27T00:00:00Z',
          siblings: ['another-branch', 'answer'],
          sibling_checkpoint_ids: ['private-branch-a', 'private-branch-b'],
          branch_index: 1,
          branch_total: 2,
        },
      ],
    )
    expect(JSON.stringify(repo.messages)).not.toMatch(/Different conversation|private-branch-[ab]/)
  })

  it('omits dangling and circular lineages without hiding a valid branch', async () => {
    const repo = await load({
      activeTipMessageId: 'valid',
      messages: ['valid', 'dangling', 'loop-a', 'loop-b'].map((id) => ({
        id,
        role: 'user',
        content: id,
      })),
      messageLinks: [
        link('valid', null),
        link('dangling', 'unavailable'),
        link('loop-a', 'loop-b'),
        link('loop-b', 'loop-a'),
      ],
    })
    expect(repo.messages.map(({ message }) => message.id)).toEqual(['valid'])
    expect(repo.headId).toBe('valid')
  })

  it('never offers the SDK a fallback sibling when the selected tip is unavailable', async () => {
    const repo = await load({
      messages: [{ id: 'sibling', role: 'assistant', content: 'Another branch' }],
      messageLinks: [link('sibling', null)],
      activeTipMessageId: 'unavailable',
    })
    expect(repo.headId).toBeNull()
    expect(repo.messages).toEqual([])
  })

  it('binds a saved UI card to its tool-only parent on the correct physical run', async () => {
    const otherRun = '657a1ce4-3491-43f4-b33e-499e62555d2f'
    const publicCallId = 'tool-ebb22f7a-33b5-5e37-86b0-b3be709a3449'
    const tool = {
      id: publicCallId,
      type: 'function',
      function: { name: 'show_note', arguments: '{}' },
    }
    const scope = { physicalRunId: physical, runId: 'public-right' }
    const card = (id: string, claims: Record<string, unknown> = {}) => ({
      id,
      role: 'activity',
      activityType: 'moldy.ui_data',
      content: {
        type: 'demo_note',
        props: { text: id },
        tool_call_id: 'provider-private-call',
        public_tool_call_id: publicCallId,
        run_id: scope.runId,
        ...claims,
      },
    })
    const cards = [
      card('Right branch note'),
      card('missing public ID', { public_tool_call_id: undefined, tool_call_id: publicCallId }),
      card('unissued public ID', { public_tool_call_id: 'unissued', public_message_id: 'right' }),
      card('wrong owner', { public_message_id: 'left', message_id: 'right' }),
      card('wrong run', { run_id: physical }),
      card('wrong tool link'),
    ]
    const repo = await load({
      activeTipMessageId: 'right',
      messages: [
        { id: 'question', role: 'user', content: 'Compare notes' },
        { id: 'left', role: 'assistant', toolCalls: [tool] },
        { id: 'right', role: 'assistant', toolCalls: [tool] },
        ...cards,
      ],
      messageLinks: [
        link('question', null),
        link('left', 'question', { physicalRunId: otherRun, runId: 'public-left' }),
        link('right', 'question', scope),
        ...cards.map(({ id }) =>
          link(id, null, {
            uiMessageId: null,
            ...scope,
            parentToolCallId: id === 'wrong tool link' ? 'another-call' : publicCallId,
          }),
        ),
      ],
    })
    const cardOwner = repo.messages.find(({ message }) => message.id === 'right')
    expect(cardOwner?.message.content.filter((part) => part.type === 'data')).toMatchObject([
      {
        data: {
          type: 'demo_note',
          props: { text: 'Right branch note' },
          public_tool_call_id: publicCallId,
          public_message_id: 'right',
        },
      },
    ])
    expect(JSON.stringify(repo.messages)).not.toContain('provider-private-call')
    const sibling = repo.messages.find(({ message }) => message.id === 'left')
    expect(JSON.stringify(sibling?.message.content)).not.toContain('Right branch note')
  })

  it('restores a bound child card inside the invoking tool and retains independent notes', async () => {
    const child = { subagentRunId: 'child-a' }
    const scope = { runId: 'public-run', physicalRunId: physical }
    const call = (id: string, name: string) => ({
      id,
      type: 'function',
      function: { name, arguments: '{}' },
    })
    const childCall = 'tool-9d08469c-2437-5b30-8591-aeb7bfaa6e27'
    const note = (id: string, claims: Record<string, unknown>) => ({
      role: 'activity',
      activityType: 'moldy.ui_data',
      id,
      ...child,
      content: { type: 'demo_note', props: { text: id }, ...claims },
    })
    const repo = await load({
      activeTipMessageId: 'parent',
      messages: [
        { id: 'question', role: 'user', content: 'Compare notes' },
        { id: 'parent', role: 'assistant', toolCalls: [call('delegate', 'task')] },
        {
          id: 'child-model',
          role: 'assistant',
          toolCalls: [call(childCall, 'show_note')],
          ...child,
        },
        note('child bound', { tool_call_id: 'private-source', public_tool_call_id: childCall }),
        note('child independent', { public_message_id: 'child-model' }),
        note('crossed parent', {
          public_message_id: 'child-model',
          public_tool_call_id: 'delegate',
        }),
      ],
      messageLinks: [
        link('question', null),
        link('parent', 'question', scope),
        link('child-model', 'parent', { ...scope, ...child }),
        ...['child bound', 'child independent', 'crossed parent'].map((id) =>
          link(id, 'child-model', { ...scope, ...child, uiMessageId: null }),
        ),
      ],
      subagents: [
        {
          ...child,
          parentMessageId: 'parent',
          parentToolCallId: 'delegate',
          name: 'worker',
          status: 'completed',
        },
      ],
    })
    const parent = repo.messages.find(({ message }) => message.id === 'parent')?.message
    const task = parent?.content.find((part) => part.type === 'tool-call')
    const nested = task?.type === 'tool-call' ? task.messages?.[0] : undefined
    expect(nested?.content.filter((part) => part.type === 'data')).toMatchObject([
      {
        data: {
          type: 'demo_note',
          props: { text: 'child bound' },
          public_tool_call_id: childCall,
          public_message_id: 'child-model',
        },
      },
      {
        data: {
          type: 'demo_note',
          props: { text: 'child independent' },
        },
      },
    ])
    expect(parent?.content.some((part) => part.type === 'data')).toBe(false)
    expect(JSON.stringify(repo.messages)).not.toContain('private-source')
  })
})
