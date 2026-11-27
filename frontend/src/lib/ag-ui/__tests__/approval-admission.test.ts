import { describe, expect, it } from 'vitest'
import { currentAgUiApprovalKey } from '../approval-admission'
import { createAgUiChatSession } from '../chat-history'
import { parseWireChatSnapshot } from '../chat-history-types'

const thread = 'conversation'
const publicRun = 'public'
const physical = 'physical'
const interrupt = {
  id: 'review-1',
  reason: 'moldy.tool_approval',
  metadata: {
    moldy: {
      action_requests: [{ name: 'send_mail', args: { to: 'a@example.invalid' } }],
      review_configs: [{ action_name: 'send_mail', allowed_decisions: ['approve', 'reject'] }],
    },
  },
}
function pause(extra: Record<string, unknown> = {}) {
  return parseWireChatSnapshot(
    {
      threadId: thread,
      protocolVersion: '1.0',
      projectionVersion: '1',
      checkpointId: 'active-checkpoint',
      runId: publicRun,
      runStatus: 'interrupted',
      latestPhysicalRunId: physical,
      interruptRunId: publicRun,
      physicalInterruptRunId: physical,
      messages: [],
      messageLinks: [],
      subagents: [],
      activities: [],
      interrupts: [interrupt],
      ...extra,
    },
    thread,
  )
}
async function selected(extra: Record<string, unknown> = {}) {
  const snapshot = pause(extra)
  const session = createAgUiChatSession({
    conversationId: thread,
    readSnapshot: async () => snapshot,
    getServerMessages: () => [],
  })
  await session.refreshRepository()
  return session
}
describe('approval admission on the selected branch', () => {
  it.each([
    { runStatus: 'running' },
    { latestPhysicalRunId: 'successor' },
    { runId: 'prior-public' },
    { checkpointId: null },
  ])('does not expose an action without durable matching parent/checkpoint %j', async (extra) => {
    const session = await selected(extra)
    const state = session.getSnapshot()
    expect(currentAgUiApprovalKey(thread, state, state.interrupts)).toBeNull()
  })
  it('matches the native set and invalidates captured votes even after returning to the same branch', async () => {
    const session = await selected()
    const selectedState = session.getSnapshot()
    const first = currentAgUiApprovalKey(thread, selectedState, selectedState.interrupts)
    expect(first).toEqual(expect.any(String))
    expect(
      currentAgUiApprovalKey(thread, selectedState, [
        { ...selectedState.interrupts[0], id: 'rotated' },
      ]),
    ).toBeNull()
    session.invalidateApproval()
    expect(
      currentAgUiApprovalKey(thread, session.getSnapshot(), selectedState.interrupts),
    ).toBeNull()
    await session.refreshRepository()
    const restored = session.getSnapshot()
    const next = currentAgUiApprovalKey(thread, restored, restored.interrupts)
    expect(next).toEqual(expect.any(String))
    expect(next).not.toBe(first)
  })
})
