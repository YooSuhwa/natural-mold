import { EventType, type ActivitySnapshotEvent } from '@ag-ui/core'
import { afterEach, expect, it, vi } from 'vitest'
import { SubagentPanelContent } from '@/components/chat/right-rail/subagent-panel-content'
import { AgUiTestChat, conversationId } from '../../../../tests/ag-ui/chat-ui-harness'
import { createFixtureAgent, parallelEvents } from '../../../../tests/ag-ui/chat-ui-fixtures'
import { render, screen, userEvent, within } from '../../../../tests/test-utils'

type Sibling = 'A' | 'B'
const publicCall = {
  A: 'tool-7d9683a9-d5de-551a-9202-2f904d873ad6',
  B: 'tool-d64c8094-e381-51ef-a040-e39c567108b9',
}
const sharedRawId = 'same-provider-call'

function childTool(target: Sibling) {
  const scope = { subagentRunId: 'child-' + target }
  const messageId = 'tool-owner-' + target
  const toolCallId = publicCall[target]
  return [
    { type: EventType.TEXT_MESSAGE_START, messageId, role: 'assistant', ...scope },
    { type: EventType.TEXT_MESSAGE_CONTENT, messageId, delta: 'Preparing ' + target, ...scope },
    { type: EventType.TEXT_MESSAGE_END, messageId, ...scope },
    {
      type: EventType.TOOL_CALL_START,
      toolCallId,
      toolCallName: 'report',
      parentMessageId: messageId,
      ...scope,
    },
    { type: EventType.TOOL_CALL_ARGS, toolCallId, delta: '{}', ...scope },
    { type: EventType.TOOL_CALL_END, toolCallId, ...scope },
    {
      type: EventType.TOOL_CALL_RESULT,
      toolCallId,
      messageId: 'result-report-' + target,
      role: 'tool',
      content: 'Ready',
      ...scope,
    },
  ]
}

function note(
  target: Sibling,
  text: string,
  tool: Sibling | undefined,
  issuer: Sibling,
  rawCallId = sharedRawId,
): ActivitySnapshotEvent {
  return {
    type: EventType.ACTIVITY_SNAPSHOT,
    subagentRunId: 'child-' + target,
    messageId: 'note-' + text,
    activityType: 'moldy.ui_data',
    content: {
      type: 'demo_note',
      props: { text },
      tool_call_id: rawCallId,
      ...(tool && { public_tool_call_id: publicCall[tool] }),
      public_message_id: 'tool-owner-' + issuer,
    },
  }
}

function childCardEvents() {
  const frames = parallelEvents()
  const firstResponse = frames.findIndex((frame) => frame.type === EventType.TEXT_MESSAGE_START)
  if (firstResponse < 0) throw new Error('Expected two active fixture siblings')
  return [
    ...frames.slice(0, firstResponse),
    ...childTool('A'),
    ...childTool('B'),
    note('A', 'Verified note A', 'A', 'A'),
    note('B', 'Verified note B', 'B', 'B'),
    note('A', 'Missing public ID', undefined, 'A', publicCall.A),
    note('A', 'Sibling call ID', 'B', 'A'),
    note('A', 'Sibling issuer', 'A', 'B'),
    ...frames.slice(firstResponse),
  ]
}

afterEach(() => vi.restoreAllMocks())

it('renders each child card only under the child whose public tool call and issuer match', async () => {
  const { agent, session, fetch } = createFixtureAgent(childCardEvents)
  const siblings: Sibling[] = ['A', 'B']
  render(
    <AgUiTestChat agent={agent} session={session} messagesVisible={false}>
      {siblings.map((target) => (
        <section key={target} aria-label={'Panel ' + target}>
          <SubagentPanelContent
            payload={{
              conversationId,
              agentName: 'researcher',
              toolCallId: 'task-' + target,
              subagentRunId: 'child-' + target,
            }}
          />
        </section>
      ))}
    </AgUiTestChat>,
  )
  await userEvent.click(screen.getByRole('button', { name: 'Send fixture' }))
  for (const target of siblings) {
    const panel = within(screen.getByRole('region', { name: 'Panel ' + target }))
    await panel.findByText('Evidence from ' + target)
    const cards = panel.getAllByTestId('data-ui-demo-note')
    expect(cards).toHaveLength(1)
    expect(cards[0]).toHaveTextContent('Verified note ' + target)
  }
  expect(fetch).toHaveBeenCalledTimes(1)
})
