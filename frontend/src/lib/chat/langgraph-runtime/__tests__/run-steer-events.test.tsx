import type { AnyStream } from '@langchain/react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { act, render, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { conversationQueryKeys } from '@/lib/query-keys/conversations'
import { RunSteerStreamSync } from '../run-steer-events'

interface ChannelOptions {
  replay: boolean
  onEvent: (event: unknown) => void
}

const { subscribe } = vi.hoisted(() => ({
  subscribe:
    vi.fn<(stream: AnyStream, channels: readonly string[], options: ChannelOptions) => void>(),
}))
vi.mock('@langchain/react', () => ({ useChannelEffect: subscribe }))

const conversationId = 'conversation-A'
const runId = 'run-A'
const steerId = 'steer-A'
const receiptsKey = conversationQueryKeys.steers(conversationId)
const otherKey = conversationQueryKeys.steers('conversation-B')
const messagesKey = conversationQueryKeys.messages(conversationId)
const payload = {
  conversation_id: conversationId,
  run_id: runId,
  steer_id: steerId,
  status: 'pending',
}

function wireEvent(method: string, data: unknown) {
  return {
    type: 'event',
    method,
    seq: 7,
    event_id: 'steer-A:steer:pending',
    params: { namespace: [], data },
  }
}

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { staleTime: Infinity, retry: false } },
  })
  const stream = {} as AnyStream
  const durable = [{ id: steerId, text: 'Persisted instruction', status: 'applied' }]
  const readReceipts = vi.fn(async () => durable)
  client.setQueryData(receiptsKey, [{ ...durable[0], status: 'pending' }])
  client.setQueryData(otherKey, [])
  client.setQueryData(messagesKey, [])
  function ObservedHistory() {
    useQuery({ queryKey: receiptsKey, queryFn: readReceipts })
    return <RunSteerStreamSync stream={stream} conversationId={conversationId} />
  }
  render(
    <QueryClientProvider client={client}>
      <ObservedHistory />
    </QueryClientProvider>,
  )
  const call = subscribe.mock.calls.at(-1)
  if (!call) throw Error('No stream subscription registered')
  const [subscribedStream, channels, options] = call
  expect(subscribedStream).toBe(stream)
  expect(channels).toContain('custom')
  expect(options.replay).toBe(true)
  return { client, readReceipts, durable, options }
}

beforeEach(() => {
  subscribe.mockClear()
})

describe('run instruction event replay', () => {
  it('ignores other conversations, lifecycle notifications and unrelated custom events', () => {
    const { client, options, readReceipts } = setup()
    act(() => {
      options.onEvent(
        wireEvent('custom', {
          name: 'moldy.run_steer',
          payload: { ...payload, conversation_id: 'conversation-B' },
        }),
      )
      options.onEvent(wireEvent('lifecycle', { name: 'moldy.run_steer', payload }))
      options.onEvent(wireEvent('custom', { name: 'moldy.compaction', payload }))
    })
    expect(readReceipts).not.toHaveBeenCalled()
    expect(client.getQueryState(otherKey)?.isInvalidated).toBe(false)
    expect(client.getQueryState(messagesKey)?.isInvalidated).toBe(false)
    expect(client.getQueryState(receiptsKey)?.isInvalidated).toBe(false)
  })

  it.each([
    ['stored named event', wireEvent('custom', { name: 'moldy.run_steer', payload })],
    ['typed event', wireEvent('custom:moldy.run_steer', payload)],
  ])('refetches the authorized list when a %s is replayed twice', async (_, event) => {
    const { client, options, readReceipts, durable } = setup()
    act(() => {
      options.onEvent(event)
      options.onEvent(event)
    })
    await waitFor(() => expect(client.getQueryData(receiptsKey)).toEqual(durable))
    expect(readReceipts).toHaveBeenCalledTimes(1)
    expect(client.getQueryState(otherKey)?.isInvalidated).toBe(false)
    expect(client.getQueryState(messagesKey)?.isInvalidated).toBe(false)

    // A late, stale notification cannot supply content or revert an applied row.
    act(() =>
      options.onEvent(
        wireEvent('custom', {
          name: 'moldy.run_steer',
          payload: { ...payload, text: 'Untrusted projection' },
        }),
      ),
    )
    await waitFor(() => expect(client.getQueryState(receiptsKey)?.fetchStatus).toBe('idle'))
    expect(client.getQueryData(receiptsKey)).toEqual(durable)
  })
})
