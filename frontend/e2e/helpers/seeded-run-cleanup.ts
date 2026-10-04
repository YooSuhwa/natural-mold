import { expect, type APIRequestContext } from '@playwright/test'

interface SeededRun {
  readonly url: string
  readonly runId: string
  readonly csrfHeaders: Readonly<Record<string, string>>
}

export async function retireSeededRun(request: APIRequestContext, run: SeededRun): Promise<void> {
  // These helper-created runs have no worker to finish them. Exercise the
  // existing stale lifecycle before deleting their fixture ownership graph.
  const aged = await request.patch(`${run.url}/runs/${run.runId}/heartbeat`, {
    headers: run.csrfHeaders,
    data: { heartbeat_age_seconds: 900 },
  })
  expect(aged.ok()).toBeTruthy()
  const swept = await request.post(`${run.url}/runs/stale-sweep`, {
    headers: run.csrfHeaders,
  })
  expect(swept.ok()).toBeTruthy()
}
