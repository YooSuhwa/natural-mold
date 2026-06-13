import { afterEach, describe, expect, it } from 'vitest'
import { getChatRuntimeMode } from '../runtime-mode'

describe('getChatRuntimeMode', () => {
  const original = process.env.NEXT_PUBLIC_CHAT_RUNTIME

  afterEach(() => {
    process.env.NEXT_PUBLIC_CHAT_RUNTIME = original
  })

  it('defaults to legacy', () => {
    delete process.env.NEXT_PUBLIC_CHAT_RUNTIME
    expect(getChatRuntimeMode()).toBe('legacy')
  })

  it('enables the LangGraph v3 runtime explicitly', () => {
    process.env.NEXT_PUBLIC_CHAT_RUNTIME = 'langgraph_v3'
    expect(getChatRuntimeMode()).toBe('langgraph_v3')
  })

  it('falls back to legacy for unknown values', () => {
    process.env.NEXT_PUBLIC_CHAT_RUNTIME = 'ag_ui'
    expect(getChatRuntimeMode()).toBe('legacy')
  })
})
