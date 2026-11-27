import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, userEvent, waitFor, within } from '../../../../tests/test-utils'
import { AgUiTestChat } from '../../../../tests/ag-ui/chat-ui-harness'
import {
  conversationId,
  createFixtureAgent,
  physicalRunId,
  savedCardSnapshot,
} from '../../../../tests/ag-ui/chat-ui-fixtures'

const issuerId = 'analysis-message'
const laterId = 'summary-message'
const publicCallId = 'tool-b3da5c8f-49bb-5b1e-830a-b587053b124e'
const sourceCallId = 'provider-call-18'
const issuerText = 'The first answer calls the reporting tool.'
const laterText = 'The second answer summarizes the report.'

function assistantText(messageId: string, delta: string) {
  return [
    { type: 'TEXT_MESSAGE_START', messageId, role: 'assistant' },
    { type: 'TEXT_MESSAGE_CONTENT', messageId, delta },
    { type: 'TEXT_MESSAGE_END', messageId },
  ]
}

function toolTurns() {
  return [
    ...assistantText(issuerId, issuerText),
    {
      type: 'TOOL_CALL_START',
      toolCallId: publicCallId,
      toolCallName: 'report_builder',
      parentMessageId: issuerId,
    },
    { type: 'TOOL_CALL_ARGS', toolCallId: publicCallId, delta: '{}' },
    { type: 'TOOL_CALL_END', toolCallId: publicCallId },
    {
      type: 'TOOL_CALL_RESULT',
      toolCallId: publicCallId,
      messageId: 'tool-result',
      role: 'tool',
      content: 'Report ready',
    },
    ...assistantText(laterId, laterText),
  ]
}

function card(messageId: string, text: string, references: Record<string, string>) {
  return {
    type: 'ACTIVITY_SNAPSHOT',
    activityType: 'moldy.ui_data',
    messageId,
    content: { type: 'demo_note', props: { text }, ...references },
  }
}

async function showRun(cards: ReturnType<typeof card>[]) {
  const { agent, session, fetch } = createFixtureAgent(() => [...toolTurns(), ...cards])
  render(<AgUiTestChat agent={agent} session={session} />)
  await userEvent.click(screen.getByRole('button', { name: 'Send fixture' }))
  await screen.findByText(laterText)
  await waitFor(() => expect(session.getSnapshot().lastTerminalPhysicalRunId).toBe(physicalRunId))
  expect(fetch).toHaveBeenCalledTimes(1)
}

afterEach(() => vi.restoreAllMocks())

describe('tool-owned AG-UI data cards', () => {
  it('shows a late card once on the answer that issued its opaque tool call', async () => {
    const note = 'Evidence from the reporting tool'
    const references = {
      public_message_id: issuerId,
      public_tool_call_id: publicCallId,
      tool_call_id: sourceCallId,
    }
    await showRun([
      card('report-card', 'Draft report', references),
      card('report-card', note, references),
    ])
    const issuer = screen.getByText(issuerText).closest<HTMLElement>('[data-message-id]')
    const later = screen.getByText(laterText).closest<HTMLElement>('[data-message-id]')
    expect(issuer).toBeInTheDocument()
    expect(later).toBeInTheDocument()
    expect(issuer).not.toBe(later)
    if (!issuer || !later) throw new Error('Expected two assistant message containers')
    await within(issuer).findByText(note)
    expect(screen.getAllByText(note)).toHaveLength(1)
    expect(within(later).queryByText(note)).not.toBeInTheDocument()
    expect(screen.queryByText('Draft report')).not.toBeInTheDocument()
  })

  it('requires the public tool ID and rejects a card claimed by another assistant answer', async () => {
    await showRun([
      card('raw-only', 'Raw call must not authorize a card', {
        public_message_id: issuerId,
        tool_call_id: publicCallId,
      }),
      card('wrong-issuer', 'Wrong issuer must not receive a card', {
        public_message_id: laterId,
        public_tool_call_id: publicCallId,
        tool_call_id: sourceCallId,
      }),
    ])
    expect(screen.queryByText('Raw call must not authorize a card')).not.toBeInTheDocument()
    expect(screen.queryByText('Wrong issuer must not receive a card')).not.toBeInTheDocument()
  })

  it('restores the tool card once on its original answer using a snapshot GET', async () => {
    const base = savedCardSnapshot()
    const note = 'Saved evidence from the report'
    const resultId = 'report-result'
    const cardId = 'saved-evidence'
    const row = (messageId: string, parentMessageId: string | null, actor = true) => ({
      messageId,
      parentMessageId,
      physicalRunId,
      runId: 'completed-run',
      uiMessageId: actor ? 'stored-' + messageId : null,
      checkpointId: base.checkpointId,
      parentCheckpointId: null,
    })
    const saved = {
      ...base,
      activeTipMessageId: laterId,
      messages: [
        { id: 'question', role: 'user', content: 'Summarize the records' },
        {
          id: issuerId,
          role: 'assistant',
          content: issuerText,
          toolCalls: [
            {
              id: publicCallId,
              type: 'function',
              function: { name: 'report_builder', arguments: '{}' },
            },
          ],
        },
        { id: resultId, role: 'tool', content: 'Report ready', toolCallId: publicCallId },
        { id: laterId, role: 'assistant', content: laterText },
        {
          id: cardId,
          role: 'activity',
          activityType: 'moldy.ui_data',
          content: {
            type: 'demo_note',
            props: { text: note },
            public_message_id: issuerId,
            public_tool_call_id: publicCallId,
            tool_call_id: sourceCallId,
          },
        },
      ],
      messageLinks: [
        row('question', null),
        row(issuerId, 'question'),
        row(resultId, issuerId),
        row(laterId, resultId),
        { ...row(cardId, issuerId, false), parentToolCallId: publicCallId },
      ],
    }
    const { agent, session, fetch } = createFixtureAgent(() => [], saved)
    render(<AgUiTestChat agent={agent} session={session} restore />)
    const issuer = (await screen.findByText(issuerText)).closest<HTMLElement>('[data-message-id]')
    const later = (await screen.findByText(laterText)).closest<HTMLElement>('[data-message-id]')
    if (!issuer || !later) throw new Error('Expected both saved assistant messages')
    await within(issuer).findByText(note)
    expect(screen.getAllByText(note)).toHaveLength(1)
    expect(within(later).queryByText(note)).not.toBeInTheDocument()
    expect(fetch.mock.calls.map(([path, options]) => [path, options?.method])).toEqual([
      [`/api/conversations/${conversationId}/ag-ui/snapshot`, 'GET'],
    ])
  })
})
