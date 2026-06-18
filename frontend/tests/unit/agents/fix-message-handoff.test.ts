import { beforeEach, describe, expect, it } from 'vitest'

import { consumeFixInitialMessage, storeFixInitialMessage } from '@/lib/agents/fix-message-handoff'

describe('fix-message-handoff', () => {
  beforeEach(() => {
    window.sessionStorage.clear()
  })

  it('stores and consumes the initial fix message once', () => {
    storeFixInitialMessage('hello')

    expect(consumeFixInitialMessage()).toBe('hello')
    expect(consumeFixInitialMessage()).toBeNull()
  })
})
