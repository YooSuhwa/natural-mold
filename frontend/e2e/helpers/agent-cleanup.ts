import { expect, type APIRequestContext } from '@playwright/test'
import { z } from 'zod'

const conversationsSchema = z.array(
  z.object({
    active_run: z
      .object({
        id: z.string(),
        status: z.enum([
          'queued',
          'running',
          'canceling',
          'completed',
          'failed',
          'interrupted',
          'canceled',
          'stale',
        ]),
      })
      .nullable(),
  }),
)

export async function waitForAgentRunsBeforeDelete(
  request: APIRequestContext,
  agentUrl: string,
): Promise<void> {
  // Shared UI fixture cleanup must wait for durable persistence, not final text.
  // The server still rejects unsafe deletion; this never retries the DELETE.
  await expect
    .poll(
      async () => {
        const response = await request.get(`${agentUrl}/conversations`)
        if (response.status() === 404) return []
        if (!response.ok()) {
          throw new Error(
            `Agent cleanup GET failed (${response.status()}): ${await response.text()}`,
          )
        }
        const body: unknown = await response.json()
        return conversationsSchema
          .parse(body)
          .flatMap(({ active_run: run }) =>
            run && ['queued', 'running', 'canceling'].includes(run.status) ? [run.id] : [],
          )
      },
      {
        timeout: 45_000,
        intervals: [250, 500, 1000],
        message: 'Agent runs must settle before cleanup',
      },
    )
    .toEqual([])
}
