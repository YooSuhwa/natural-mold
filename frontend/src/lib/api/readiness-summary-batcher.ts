import { readinessApi } from './readiness'
import type { ReadinessSummary } from '@/lib/types/readiness'

/** The summary route accepts at most this many ids per call (plan S20). */
export const SUMMARY_BATCH_SIZE = 50

type Fetch = (agentIds: readonly string[]) => Promise<Record<string, ReadinessSummary>>

interface Waiter {
  resolve: (summary: ReadinessSummary | null) => void
  reject: (error: unknown) => void
}

/**
 * Collects the ids every agent card asks for in the same tick and sends one
 * `POST /api/agents/readiness-summary` per 50 ids (plan S20 "보이는 카드 ID로 한 번").
 * `null` means the server left the agent out (not runnable by the caller).
 */
export function createReadinessSummaryBatcher(fetchSummaries: Fetch = readinessApi.summary) {
  let queue = new Map<string, Waiter[]>()
  let scheduled = false

  async function flush(): Promise<void> {
    scheduled = false
    const batch = queue
    queue = new Map()
    const ids = [...batch.keys()]
    const chunks: string[][] = []
    for (let i = 0; i < ids.length; i += SUMMARY_BATCH_SIZE) {
      chunks.push(ids.slice(i, i + SUMMARY_BATCH_SIZE))
    }
    await Promise.all(
      chunks.map(async (chunk) => {
        try {
          const answer = await fetchSummaries(chunk)
          for (const id of chunk) {
            for (const waiter of batch.get(id) ?? []) waiter.resolve(answer[id] ?? null)
          }
        } catch (error) {
          for (const id of chunk) for (const waiter of batch.get(id) ?? []) waiter.reject(error)
        }
      }),
    )
  }

  return {
    load(agentId: string): Promise<ReadinessSummary | null> {
      return new Promise((resolve, reject) => {
        queue.set(agentId, [...(queue.get(agentId) ?? []), { resolve, reject }])
        if (!scheduled) {
          scheduled = true
          setTimeout(() => void flush(), 0)
        }
      })
    },
  }
}

export const readinessSummaryBatcher = createReadinessSummaryBatcher()
