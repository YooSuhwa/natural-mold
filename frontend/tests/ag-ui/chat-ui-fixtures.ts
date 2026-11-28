import { createHash } from 'node:crypto'
import { http, HttpResponse } from 'msw'
import { server } from '../setup'
import * as apiClient from '@/lib/api/client'
import { createAgUiChatSession } from '@/lib/ag-ui/chat-history'
import { MoldyAgUiAgent } from '@/lib/ag-ui/moldy-agent'
import { vi } from 'vitest'

type Frame = Record<string, unknown>
type RunIds = { threadId: string; runId: string }

export const conversationId = '9deef6a4-33fc-4ba5-b788-cbf8c5ae16aa'
export const physicalRunId = 'c67debcf-19d4-4c16-8dd2-9c8a52a04bc5'
export const bindingId = '1264d8f6-bca0-44e7-b5d5-fd0a7112e0f4'
export const widgetSourceCallId = 'report-tool'

export function createFixtureAgent(
  buildEvents: (runId: string) => Frame[],
  savedSnapshot?: object,
) {
  server.use(
    http.get(apiClient.API_BASE + '/api/conversations/' + conversationId + '/child-tasks', () =>
      HttpResponse.json({
        enabled: false,
        allow_legacy_details: true,
        items: [],
        next_offset: null,
      }),
    ),
  )
  const session = createAgUiChatSession({
    conversationId,
    getServerMessages: () => [],
    readSnapshot: (signal) => agent.readSnapshot({ signal }),
  })
  const agent: MoldyAgUiAgent = new MoldyAgUiAgent({
    conversationId,
    onPublicEvent: session.onPublicEvent,
    onAccepted: ({ publicRunId, physicalRunId }) =>
      session.onRunAccepted({ runId: publicRunId, physicalRunId }),
  })
  const fetch = vi.spyOn(apiClient, 'apiFetchResponse').mockImplementation(async (url, options) => {
    if (savedSnapshot && url.endsWith('/ag-ui/snapshot') && options?.method === 'GET') {
      return Response.json(savedSnapshot)
    }
    if (!url.endsWith('/ag-ui/runs') || options?.method !== 'POST') {
      throw new Error('Unexpected fixture request: ' + url)
    }
    const request: RunIds = JSON.parse(String(options?.body))
    const { threadId, runId } = request
    const frames = [
      { type: 'RUN_STARTED', threadId, runId, protocolVersion: '1.0' },
      ...buildEvents(runId),
      { type: 'RUN_FINISHED', threadId, runId, outcome: { type: 'success' } },
    ]
    return new Response(frames.map((frame) => 'data: ' + JSON.stringify(frame) + '\n\n').join(''), {
      headers: {
        'content-type': 'text/event-stream',
        'X-Run-Id': runId,
        'X-Moldy-Run-Id': physicalRunId,
      },
    })
  })
  return { fetch, agent, session }
}

function toolCall(id: string, name: string, args: Record<string, unknown>): Frame[] {
  return [
    {
      type: 'TOOL_CALL_START',
      toolCallId: id,
      toolCallName: name,
      parentMessageId: 'parent-model',
    },
    { type: 'TOOL_CALL_ARGS', toolCallId: id, delta: JSON.stringify(args) },
    { type: 'TOOL_CALL_END', toolCallId: id },
  ]
}

function text(id: string, content: string, subagentRunId?: string): Frame[] {
  const scope = subagentRunId ? { subagentRunId } : {}
  return [
    { type: 'TEXT_MESSAGE_START', messageId: id, role: 'assistant', ...scope },
    { type: 'TEXT_MESSAGE_CONTENT', messageId: id, delta: content, ...scope },
    { type: 'TEXT_MESSAGE_END', messageId: id, ...scope },
  ]
}

function activity(type: string, messageId: string, content: Record<string, unknown>) {
  return { type: 'ACTIVITY_SNAPSHOT', activityType: 'moldy.' + type, messageId, content }
}

function completedCardActivities() {
  return [
    activity('compaction', 'summary-1', {
      state: 'done',
      history_id: 'history_0123456789abcdef01234567',
      public_message_id: 'assistant-report',
      cutoff_index: 3,
    }),
    activity('ui_data', 'show-table', {
      public_message_id: 'assistant-report',
      type: 'data_table',
      props: {
        title: 'Record groups',
        columns: [{ key: 'category', header: 'Category' }],
        rows: [{ category: 'Receipts' }],
      },
    }),
    activity('ui_data', 'unknown-ui', {
      type: 'unknown_type',
      props: { text: 'hidden-unknown-data' },
    }),
  ]
}

export function cardEvents() {
  return [
    activity('compaction', 'summary-1', { state: 'running' }),
    ...text('assistant-report', 'The records are ready.'),
    ...completedCardActivities(),
  ]
}

export function savedCardSnapshot() {
  const checkpointId = 'checkpoint-for-report'
  const messages = [
    { id: 'initial-input', role: 'user', content: 'Summarize the records' },
    { id: 'assistant-report', role: 'assistant', content: 'The records are ready.' },
  ]
  const cards = completedCardActivities().map(({ messageId, activityType, content }) => ({
    id: messageId,
    role: 'activity',
    activityType,
    content,
  }))
  return {
    threadId: conversationId,
    protocolVersion: '1.0',
    projectionVersion: '1',
    activeTipMessageId: 'assistant-report',
    checkpointId,
    activeRunId: null,
    physicalRunId: null,
    interruptRunId: null,
    physicalInterruptRunId: null,
    messages: [...messages, ...cards],
    messageLinks: [...messages, ...cards].map((message, index) => ({
      messageId: message.id,
      uiMessageId: message.role === 'activity' ? null : 'stored-' + message.id,
      runId: 'completed-run',
      physicalRunId,
      checkpointId,
      parentCheckpointId: null,
      parentMessageId:
        message.role === 'activity' ? 'assistant-report' : index === 0 ? null : 'initial-input',
    })),
    subagents: [],
    interrupts: [],
    cursor: null,
  }
}

export function widgetEvents(runId: string) {
  const issuance = JSON.stringify([runId, null, 'parent-model', widgetSourceCallId])
  const publicToolCallId = 'tool-' + createHash('sha256').update(issuance).digest('hex')
  return [
    ...toolCall(publicToolCallId, 'report_view', { topic: 'receipts' }),
    {
      type: 'TOOL_CALL_RESULT',
      toolCallId: publicToolCallId,
      messageId: 'widget-result',
      role: 'tool',
      content: 'Report ready',
    },
    activity('mcp_app', 'app-pointer', {
      run_id: physicalRunId,
      tool_call_id: widgetSourceCallId,
      public_tool_call_id: publicToolCallId,
      binding_id: bindingId,
      public_message_id: 'parent-model',
    }),
    ...text('assistant-report', 'The records are ready.'),
  ]
}

export function parallelEvents() {
  const delegate = (target: string) => [
    ...toolCall('task-' + target, 'task', {
      subagent_type: 'researcher',
      description: 'Investigate ' + target,
    }),
    {
      type: 'SUBAGENT_STARTED',
      subagentRunId: 'child-' + target,
      parentToolCallId: 'task-' + target,
      parentMessageId: 'parent-model',
      name: 'researcher',
    },
  ]
  const finish = (target: string) => [
    ...text('message-' + target, 'Evidence from ' + target, 'child-' + target),
    { type: 'SUBAGENT_FINISHED', subagentRunId: 'child-' + target, result: 'Finished ' + target },
    {
      type: 'TOOL_CALL_RESULT',
      toolCallId: 'task-' + target,
      messageId: 'result-' + target,
      role: 'tool',
      content: 'Finished ' + target,
    },
  ]
  return [
    ...delegate('A'),
    ...delegate('B'),
    ...finish('B'),
    ...finish('A'),
    ...text('root-answer', 'Both reports are ready.'),
  ]
}
