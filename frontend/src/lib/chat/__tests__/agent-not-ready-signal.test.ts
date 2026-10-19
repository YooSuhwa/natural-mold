import { describe, expect, it, vi } from 'vitest'

import {
  announceIfAgentNotReady,
  onAgentNotReady,
  originOfPath,
  signalMatches,
} from '../agent-not-ready-signal'

const details = {
  overall: 'blocked',
  items: [{ kind: 'tool', required: true, result: 'no_permission', count: 1 }],
}

function response(status: number, code: string): Response {
  return new Response(JSON.stringify({ error: { code, message: 'm', details } }), { status })
}

describe('agent_not_ready signal', () => {
  it('announces a 409 agent_not_ready with its readiness body and leaves the body readable', async () => {
    const listener = vi.fn()
    const off = onAgentNotReady(listener)
    const res = response(409, 'agent_not_ready')
    await announceIfAgentNotReady(res, { agentId: 'a-1', conversationId: 'c-1' })
    off()
    expect(listener).toHaveBeenCalledWith({ details, agentId: 'a-1', conversationId: 'c-1' })
    await expect(res.json()).resolves.toMatchObject({ error: { code: 'agent_not_ready' } })
  })

  it('ignores other conflicts and errors', async () => {
    const listener = vi.fn()
    const off = onAgentNotReady(listener)
    await announceIfAgentNotReady(response(409, 'MULTITASK_REJECTED'))
    await announceIfAgentNotReady(response(403, 'agent_not_ready'))
    off()
    expect(listener).not.toHaveBeenCalled()
  })

  it('matches a refusal to the screen by body agent id, origin agent, then conversation', () => {
    const body = { overall: 'blocked' as const, items: [], agent_id: 'a-1' }
    expect(signalMatches({ details: body, agentId: 'other' }, 'a-1')).toBe(true)
    expect(signalMatches({ details: null, agentId: 'a-2' }, 'a-1')).toBe(false)
    expect(signalMatches({ details: null, conversationId: 'c-1' }, 'a-1', 'c-1')).toBe(true)
    expect(signalMatches({ details: null }, 'a-1', 'c-1')).toBe(false)
    expect(originOfPath('/api/agents/a-9/conversations/start')).toEqual({
      agentId: 'a-9',
      conversationId: null,
    })
    expect(originOfPath('/api/conversations/c-9/messages').conversationId).toBe('c-9')
  })
})
