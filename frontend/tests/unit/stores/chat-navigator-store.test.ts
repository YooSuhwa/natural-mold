import { createStore } from 'jotai'
import { beforeEach, describe, expect, it } from 'vitest'
import {
  agentSortAtom,
  conversationRuntimeStatusAtom,
  expandedAgentIdsAtom,
  expandedListScopesAtom,
  navigatorModeAtom,
  sessionSortAtom,
  shortcutPreviewActiveAtom,
  singleExpandedAgentAtom,
} from '@/lib/stores/chat-navigator-store'

describe('chat navigator store', () => {
  beforeEach(() => {
    // atomWithStorage가 다른 테스트의 localStorage 값을 읽지 않도록 격리한다
    window.localStorage.clear()
  })

  it('exposes the default navigator preferences', () => {
    const store = createStore()

    expect(store.get(navigatorModeAtom)).toBe('agent_grouped')
    expect(store.get(agentSortAtom)).toBe('recent')
    expect(store.get(sessionSortAtom)).toBe('updated')
    expect(store.get(singleExpandedAgentAtom)).toBe(false)
    expect(store.get(expandedAgentIdsAtom)).toEqual([])
    expect(store.get(expandedListScopesAtom)).toEqual([])
  })

  it('keeps shortcut preview and runtime status outside persisted preferences', () => {
    const store = createStore()

    expect(store.get(shortcutPreviewActiveAtom)).toBe(false)
    expect(store.get(conversationRuntimeStatusAtom)).toEqual({})
  })
})
