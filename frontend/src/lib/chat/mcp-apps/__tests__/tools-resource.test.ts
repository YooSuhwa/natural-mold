import { describe, expect, it, vi } from 'vitest'
import { Tools } from '@assistant-ui/react'
import { createMoldyMcpAppRenderer } from '../renderer'
import { ALL_TOOLKIT, createMoldyChatTools } from '../../tool-ui-registry'

vi.mock('@assistant-ui/react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@assistant-ui/react')>()
  return { ...actual, Tools: vi.fn((options) => options) }
})

vi.mock('../renderer', () => ({
  createMoldyMcpAppRenderer: vi.fn(() => ({ render: vi.fn() })),
}))

describe('MCP Apps Tools resource', () => {
  it('binds the main chat renderer to its mounted conversation', () => {
    // Given a main thread with backend-authorized conversation provenance.
    createMoldyChatTools(ALL_TOOLKIT, 'conversation-1')

    // Then only that thread receives an MCP Apps renderer resource.
    expect(createMoldyMcpAppRenderer).toHaveBeenCalledWith({ conversationId: 'conversation-1' })
    expect(Tools).toHaveBeenCalledWith(
      expect.objectContaining({ toolkit: ALL_TOOLKIT, mcpApp: expect.any(Object) }),
    )
  })

  it('omits the renderer when no authorized conversation exists', () => {
    // Given a draft or side surface without a real conversation/run binding.
    vi.mocked(Tools).mockClear()
    vi.mocked(createMoldyMcpAppRenderer).mockClear()
    createMoldyChatTools(ALL_TOOLKIT)

    // Then ordinary tool rendering remains active without invented provenance.
    expect(createMoldyMcpAppRenderer).not.toHaveBeenCalled()
    expect(Tools).toHaveBeenCalledWith({ toolkit: ALL_TOOLKIT })
  })
})
