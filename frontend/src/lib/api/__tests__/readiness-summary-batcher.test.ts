import { describe, expect, it, vi } from 'vitest'

import { createReadinessSummaryBatcher } from '../readiness-summary-batcher'

const ready = { overall: 'ready' as const, counts: { ready: 1 } }

describe('createReadinessSummaryBatcher', () => {
  it('sends the ids of one tick in a single call and answers null for omitted agents', async () => {
    const fetch = vi.fn().mockResolvedValue({ a: ready })
    const batcher = createReadinessSummaryBatcher(fetch)
    const answers = await Promise.all([batcher.load('a'), batcher.load('b'), batcher.load('a')])
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch).toHaveBeenCalledWith(['a', 'b'])
    expect(answers).toEqual([ready, null, ready])
  })

  it('splits more than 50 ids into chunks of 50', async () => {
    const fetch = vi.fn().mockResolvedValue({})
    const batcher = createReadinessSummaryBatcher(fetch)
    await Promise.all(Array.from({ length: 51 }, (_, i) => batcher.load(`agent-${i}`)))
    expect(fetch.mock.calls.map(([ids]) => ids.length)).toEqual([50, 1])
  })

  it('rejects every waiter of a failed chunk', async () => {
    const batcher = createReadinessSummaryBatcher(vi.fn().mockRejectedValue(new Error('down')))
    await expect(batcher.load('a')).rejects.toThrow('down')
  })
})
