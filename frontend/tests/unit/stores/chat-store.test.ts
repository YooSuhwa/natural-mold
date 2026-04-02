import { describe, it, expect } from 'vitest'
import { createStore } from 'jotai'
import {
  streamingMessageAtom,
  streamingToolCallsAtom,
  isStreamingAtom,
  type StreamingToolCall,
} from '@/lib/stores/chat-store'

describe('chat-store atoms', () => {
  it('streamingMessageAtom defaults to null', () => {
    const store = createStore()
    expect(store.get(streamingMessageAtom)).toBeNull()
  })

  it('streamingMessageAtom can be set and read', () => {
    const store = createStore()
    const message = { id: 'msg-1', content: 'Hello' }
    store.set(streamingMessageAtom, message)
    expect(store.get(streamingMessageAtom)).toEqual(message)
  })

  it('streamingToolCallsAtom defaults to empty array', () => {
    const store = createStore()
    expect(store.get(streamingToolCallsAtom)).toEqual([])
  })

  it('streamingToolCallsAtom can hold tool calls', () => {
    const store = createStore()
    const toolCalls: StreamingToolCall[] = [
      { name: 'web_search', status: 'calling', params: { query: 'test' } },
      { name: 'scraper', status: 'completed', result: 'done' },
    ]
    store.set(streamingToolCallsAtom, toolCalls)
    expect(store.get(streamingToolCallsAtom)).toEqual(toolCalls)
    expect(store.get(streamingToolCallsAtom)).toHaveLength(2)
  })

  it('isStreamingAtom defaults to false', () => {
    const store = createStore()
    expect(store.get(isStreamingAtom)).toBe(false)
  })

  it('isStreamingAtom can be toggled', () => {
    const store = createStore()
    store.set(isStreamingAtom, true)
    expect(store.get(isStreamingAtom)).toBe(true)
    store.set(isStreamingAtom, false)
    expect(store.get(isStreamingAtom)).toBe(false)
  })
})
