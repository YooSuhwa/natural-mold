import type { Message } from '@ag-ui/core'
import type { AgUiApprovalDecision, AgUiMessageLink } from './chat-history-types'

type Decision = AgUiApprovalDecision['decision']

function ownerKey(messageId: string, physicalRunId: string, childId: string | undefined) {
  return JSON.stringify([messageId, physicalRunId, childId ?? null])
}

/** An individual action can be decided once. Contradictory proofs authorize no label. */
export function indexApprovalDecisions(receipts: readonly AgUiApprovalDecision[]) {
  const actions = new Map<string, AgUiApprovalDecision | null>()
  for (const receipt of receipts) {
    const key = JSON.stringify([receipt.physicalRunId, receipt.interruptId, receipt.actionIndex])
    actions.set(key, actions.has(key) ? null : receipt)
  }
  const issuers = new Map<string, AgUiApprovalDecision[]>()
  for (const receipt of actions.values()) {
    if (!receipt) continue
    const key = ownerKey(receipt.messageId, receipt.physicalRunId, receipt.subagentRunId)
    const group = issuers.get(key)
    if (group) group.push(receipt)
    else issuers.set(key, [receipt])
  }
  return issuers
}

export function approvalDecisionsForMessage(
  message: Message,
  link: AgUiMessageLink,
  issuers: ReturnType<typeof indexApprovalDecisions>,
): ReadonlyMap<string, Decision> | undefined {
  if (
    message.role !== 'assistant' ||
    message.id !== link.messageId ||
    message.subagentRunId !== link.subagentRunId ||
    !link.physicalRunId
  )
    return
  const receipts = issuers.get(ownerKey(message.id, link.physicalRunId, link.subagentRunId))
  if (!receipts?.length) return

  const calls = message.toolCalls ?? []
  const accepted = new Map<string, Decision>()
  for (const receipt of receipts) {
    const callId = receipt.toolCallId
    if (
      calls.filter((call) => call.id === callId).length === 1 &&
      receipts.filter((row) => row.toolCallId === callId).length === 1
    ) {
      accepted.set(callId, receipt.decision)
    }
  }
  return accepted.size ? accepted : undefined
}

/** This metadata is attached by the authenticated history projection to the issuing assistant. */
export function restoredToolDecision(
  custom: Readonly<Record<string, unknown>>,
  toolCallId: string,
) {
  const labels = custom.moldyToolApprovalDecisions
  if (!labels || typeof labels !== 'object' || !Object.hasOwn(labels, toolCallId)) return null
  const value: unknown = (labels as Record<string, unknown>)[toolCallId]
  return value === 'approve' || value === 'edit' || value === 'reject' ? value : null
}
