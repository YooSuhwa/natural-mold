import type { AssistantMessage } from '@ag-ui/core'
import { describe, expect, it } from 'vitest'
import { approvalDecisionsForMessage, indexApprovalDecisions } from '../approval-decisions'
import type { AgUiApprovalDecision, AgUiMessageLink } from '../chat-history-types'

const receipt: AgUiApprovalDecision = {
  messageId: 'source-message',
  toolCallId: 'tool',
  physicalRunId: 'original-run',
  actionIndex: 1,
  interruptId: 'interrupt',
  decision: 'edit',
}
const link: AgUiMessageLink = {
  messageId: receipt.messageId,
  physicalRunId: receipt.physicalRunId,
  runId: 'public-run',
  uiMessageId: null,
  checkpointId: 'checkpoint',
  parentMessageId: null,
  parentCheckpointId: null,
}
const call = { id: 'tool', type: 'function' as const, function: { name: 'demo', arguments: '{}' } }
const message: AssistantMessage = { id: receipt.messageId, role: 'assistant', toolCalls: [call] }

describe('historical approval receipts', () => {
  it('marks only the unique issued call under the same original physical run and lane', () => {
    const index = indexApprovalDecisions([receipt])
    expect(approvalDecisionsForMessage(message, link, index)?.get(call.id)).toBe('edit')
    expect(
      approvalDecisionsForMessage({ ...message, id: 'different-issuer' }, link, index),
    ).toBeUndefined()
    expect(
      approvalDecisionsForMessage({ ...message, subagentRunId: 'hidden' }, link, index),
    ).toBeUndefined()
    expect(
      approvalDecisionsForMessage(message, { ...link, physicalRunId: 'resumed-run' }, index),
    ).toBeUndefined()
    expect(approvalDecisionsForMessage({ ...message, toolCalls: [] }, link, index)).toBeUndefined()
    expect(
      approvalDecisionsForMessage({ ...message, toolCalls: [call, call] }, link, index),
    ).toBeUndefined()
  })
  it('withholds a decision when one action claims two owners or two decisions claim one tool', () => {
    const repeatedAction = indexApprovalDecisions([
      receipt,
      { ...receipt, messageId: 'sibling', decision: 'reject' },
    ])
    expect(approvalDecisionsForMessage(message, link, repeatedAction)).toBeUndefined()
    const repeatedTool = indexApprovalDecisions([
      receipt,
      { ...receipt, actionIndex: 2, decision: 'reject' },
    ])
    expect(approvalDecisionsForMessage(message, link, repeatedTool)).toBeUndefined()
  })
})
