import type { Interrupt } from '@ag-ui/core'
import {
  fromThreadMessageLike,
  type ThreadAssistantMessage,
  type ThreadMessage,
} from '@assistant-ui/react'
import { describe, expect, it } from 'vitest'
import { restoreAgUiInterrupts } from '../chat-history-interrupts'

const complete = { type: 'complete', reason: 'stop' } as const

function assistant(id: string): ThreadAssistantMessage {
  return {
    role: 'assistant',
    id,
    createdAt: new Date(0),
    content: [],
    status: complete,
    metadata: {
      custom: {},
      unstable_state: null,
      unstable_data: [],
      unstable_annotations: [],
      steps: [],
    },
  }
}

function ask(id: string, question: string, subagentRunId?: string): Interrupt {
  return {
    id,
    subagentRunId,
    reason: 'moldy.ask_user',
    metadata: { moldy: { type: 'ask_user', question, options: ['yes', 'no'] } },
  }
}

const rootContext = { runId: 'run', headId: 'head', children: [] }

describe('restoring pending AG-UI interrupts', () => {
  it('adds a stable approval head to the active user turn and leaves earlier branches untouched', () => {
    const user = fromThreadMessageLike(
      { id: 'head', role: 'user', content: 'Ask' },
      'head',
      complete,
    )
    const past = assistant('same-role-other-branch')
    const source = [past, user]
    const interrupted = [ask('current', 'Choose a tool')]
    const result = restoreAgUiInterrupts(source, interrupted, rootContext)
    expect(result.slice(0, 2)).toEqual(source)
    expect(result[2]).toMatchObject({
      role: 'assistant',
      status: { type: 'requires-action', reason: 'interrupt' },
      metadata: { custom: { moldyInterruptParentId: 'head', agui: { interrupts: interrupted } } },
      content: [
        { type: 'tool-call', toolName: 'ask_user', args: { hitl_interrupt_id: 'current' } },
      ],
    })
    expect(restoreAgUiInterrupts(source, interrupted, rootContext)[2].id).toBe(result[2].id)
    expect(restoreAgUiInterrupts(source, interrupted, { ...rootContext, headId: 'gone' })).toEqual(
      source,
    )
  })

  it('uses a named call once, preserves the SDK interrupt set, and displays a workflow wait without an action', () => {
    const head = {
      ...assistant('head'),
      content: [
        {
          type: 'tool-call' as const,
          toolName: 'send',
          toolCallId: 'unrelated',
          args: {},
          argsText: '{}',
        },
        {
          type: 'tool-call' as const,
          toolName: 'send',
          toolCallId: 'approved',
          args: {},
          argsText: '{}',
        },
      ],
    }
    const interrupts = [
      {
        id: 'approval',
        reason: 'moldy.tool_approval',
        toolCallId: 'approved',
        metadata: {
          moldy: {
            action_requests: [{ name: 'send', args: { recipients: ['recipient'] } }],
            review_configs: [{ action_name: 'send', allowed_decisions: ['approve', 'reject'] }],
          },
        },
      },
      { id: 'wait', reason: 'moldy.workflow_wait', message: 'Workflow pending.' },
    ]
    const restored = restoreAgUiInterrupts([head], interrupts, rootContext)
    expect(restored[0]).toMatchObject({
      metadata: { custom: { agui: { interrupts } } },
      content: [
        head.content[0],
        {
          toolCallId: 'approved',
          toolName: 'request_approval',
          args: {
            hitl_interrupt_id: 'approval',
            hitl_action_index: 0,
            tool_args: { recipients: ['recipient'] },
          },
        },
        { type: 'text', text: 'Workflow pending.' },
      ],
    })
  })

  it('replaces only an unambiguous unresolved action when the checkpoint omits the tool call ID', () => {
    const raw = {
      type: 'tool-call' as const,
      toolName: 'search',
      toolCallId: 'candidate',
      args: { text: 'x', limit: 3 },
      argsText: '{"text":"x","limit":3}',
    }
    const approval: Interrupt = {
      id: 'approval',
      reason: 'moldy.tool_approval',
      metadata: {
        moldy: {
          action_requests: [{ name: 'search', args: { limit: 3, text: 'x' } }],
          review_configs: [{ action_name: 'search', allowed_decisions: ['approve'] }],
        },
      },
    }
    const head = assistant('head')
    const completed = { ...raw, toolCallId: 'old', result: 'completed' }
    const matched = restoreAgUiInterrupts(
      [{ ...head, content: [completed, raw] }],
      [approval],
      rootContext,
    )
    expect(matched[0]).toMatchObject({
      content: [
        completed,
        {
          toolName: 'request_approval',
          toolCallId: 'candidate',
          args: { approval_id: 'candidate', hitl_interrupt_id: 'approval' },
        },
      ],
    })
    const duplicate = { ...raw, toolCallId: 'second' }
    const ambiguous = restoreAgUiInterrupts(
      [{ ...head, content: [raw, duplicate] }],
      [approval],
      rootContext,
    )
    expect(ambiguous[0]).toMatchObject({
      content: [
        raw,
        duplicate,
        {
          toolName: 'request_approval',
          toolCallId: 'approval:0',
        },
      ],
    })
  })

  it('shows a read-only batch as notices across root and child while retaining both SDK IDs', () => {
    const rawForm = {
      type: 'tool-call' as const,
      toolName: 'ask_user',
      toolCallId: 'native',
      args: { question: 'Choose' },
      argsText: '{"question":"Choose"}',
    }
    const child = { ...assistant('child'), content: [rawForm] }
    const task = {
      type: 'tool-call' as const,
      toolName: 'task',
      toolCallId: 'spawn',
      args: {},
      argsText: '{}',
      messages: [child],
    }
    const history = [{ ...assistant('head'), content: [rawForm, task] }]
    const interrupts: Interrupt[] = [
      { ...ask('ask', 'Choose'), message: 'Root question' },
      {
        id: 'old',
        subagentRunId: 'child',
        reason: 'moldy.ask_user',
        message: 'Historical child question',
        metadata: { moldy: { actionable: false } },
      },
    ]
    const restored = restoreAgUiInterrupts(history, interrupts, {
      ...rootContext,
      children: [
        {
          subagentRunId: 'child',
          parentToolCallId: 'spawn',
          parentMessageId: 'head',
          name: 'researcher',
          status: 'interrupted',
          interruptIds: ['old'],
        },
      ],
    })
    expect(restored[0]).toMatchObject({
      metadata: { custom: { agui: { interrupts } } },
      content: [
        {
          toolCallId: 'spawn',
          messages: [
            {
              id: 'child',
              content: [{ type: 'text', text: 'Historical child question' }],
              metadata: { custom: { agui: { interrupts: [interrupts[1]] } } },
            },
          ],
        },
        { type: 'text', text: 'Root question' },
      ],
    })
  })

  it('attaches current cards only to the verified nested execution', () => {
    const call = (owner: string) => ({
      type: 'tool-call' as const,
      toolName: 'task',
      toolCallId: 'spawn',
      args: {},
      argsText: '{}',
      messages: [assistant(owner)],
    })
    const source: ThreadMessage[] = [
      { ...assistant('previous'), content: [call('child')] },
      { ...assistant('head'), content: [call('child'), call('sibling')] },
    ]
    const interrupt = ask('pick', 'Choose', 'child')
    const restored = restoreAgUiInterrupts(source, [interrupt], {
      ...rootContext,
      children: [
        {
          subagentRunId: 'child',
          parentToolCallId: 'spawn',
          parentMessageId: 'head',
          name: 'researcher',
          status: 'interrupted',
          interruptIds: ['pick'],
        },
      ],
    })
    expect(restored[0]).toEqual(source[0])
    expect(restored[1]).toMatchObject({
      metadata: { custom: { agui: { interrupts: [interrupt] } } },
      content: [
        {
          toolCallId: 'spawn',
          messages: [
            {
              id: 'child',
              content: [{ toolName: 'ask_user', args: { hitl_interrupt_id: 'pick' } }],
            },
          ],
        },
        { toolCallId: 'spawn', messages: [{ id: 'sibling', content: [] }] },
      ],
    })
  })
})
