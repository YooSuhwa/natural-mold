import { afterEach, describe, expect, it, vi } from 'vitest'
import { getChatRuntimeMode } from '../runtime-mode'

describe('getChatRuntimeMode', () => {
  afterEach(() => vi.unstubAllEnvs())

  it.each(['legacy', 'langgraph_v3', 'ag_ui'] as const)('selects %s explicitly', (mode) => {
    vi.stubEnv('NEXT_PUBLIC_CHAT_RUNTIME', mode)
    expect(getChatRuntimeMode()).toBe(mode)
  })

  it.each([undefined, '', 'ag-ui', 'AG_UI'])('defaults to LangGraph v3 for %s', (mode) => {
    vi.stubEnv('NEXT_PUBLIC_CHAT_RUNTIME', mode)
    expect(getChatRuntimeMode()).toBe('langgraph_v3')
  })
})
