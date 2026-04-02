import { describe, it, expect } from 'vitest'
import { creationSessionApi } from '@/lib/api/creation-session'
import { mockCreationSession, mockCreationMessageResult, mockAgent } from '../../mocks/fixtures'

describe('creationSessionApi', () => {
  it('start() creates a new session', async () => {
    const session = await creationSessionApi.start()
    expect(session).toEqual(mockCreationSession)
    expect(session.status).toBe('in_progress')
  })

  it('get() returns session by id', async () => {
    const session = await creationSessionApi.get('session-1')
    expect(session.id).toBe('session-1')
    expect(session.conversation_history).toHaveLength(1)
  })

  it('sendMessage() sends content and returns result', async () => {
    const result = await creationSessionApi.sendMessage('session-1', 'I want a research agent')
    expect(result).toEqual(mockCreationMessageResult)
    expect(result.role).toBe('assistant')
    expect(result.current_phase).toBe(2)
    expect(result.draft_config?.name).toBe('My Agent')
  })

  it('confirm() finalizes session and returns agent', async () => {
    const agent = await creationSessionApi.confirm('session-1')
    expect(agent.id).toBe('agent-from-session')
    expect(agent.name).toBe(mockAgent.name)
  })

  it('sendMessage() returns suggested replies', async () => {
    const result = await creationSessionApi.sendMessage('session-1', 'test message')
    expect(result.suggested_replies).not.toBeNull()
    expect(result.suggested_replies?.options).toContain('Web Search')
    expect(result.suggested_replies?.multi_select).toBe(true)
    expect(result.recommended_tools).toHaveLength(1)
  })
})
