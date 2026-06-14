import { afterEach, describe, expect, it } from 'vitest'
import { getChatRuntimeMode } from '../runtime-mode'

describe('getChatRuntimeMode', () => {
  const original = process.env.NEXT_PUBLIC_CHAT_RUNTIME

  afterEach(() => {
    process.env.NEXT_PUBLIC_CHAT_RUNTIME = original
  })

  it('defaults to LangGraph v3', () => {
    delete process.env.NEXT_PUBLIC_CHAT_RUNTIME
    expect(getChatRuntimeMode()).toBe('langgraph_v3')
  })

  it('enables the legacy runtime explicitly', () => {
    process.env.NEXT_PUBLIC_CHAT_RUNTIME = 'legacy'
    expect(getChatRuntimeMode()).toBe('legacy')
  })

  it('falls back to LangGraph v3 for unknown values', () => {
    process.env.NEXT_PUBLIC_CHAT_RUNTIME = 'ag_ui'
    expect(getChatRuntimeMode()).toBe('langgraph_v3')
  })
})
