import type { Page } from '@playwright/test'

declare global {
  interface Window {
    moldySelectedApprovalRead?: (status: number, selection: unknown) => Promise<void>
    moldyStopApprovalObserver?: () => void
  }
}

/** Withhold the original response at the point where product code awaits fetch. */
export async function observeSelectedApprovalReads(
  page: Page,
  snapshotUrl: string,
  inspect: (status: number, selection: unknown) => Promise<void>,
) {
  await page.exposeBinding(
    'moldySelectedApprovalRead',
    async (source, status: number, selection: unknown) => {
      if (source.frame === page.mainFrame()) await inspect(status, selection)
    },
  )
  await page.addInitScript((expectedUrl: string) => {
    if (window.top !== window) return
    let watching = true
    window.moldyStopApprovalObserver = () => {
      watching = false
    }
    const fetch = window.fetch.bind(window)
    window.fetch = async (input, init) => {
      const request = input instanceof Request ? input : undefined
      const url = new URL(request?.url ?? String(input), location.href)
      const method = (init?.method ?? request?.method ?? 'GET').toUpperCase()
      // Receipt lookups with ?runId, other conversations, and writes pass through.
      if (!watching || method !== 'GET' || url.href !== expectedUrl) return fetch(input, init)
      const response = await fetch(input, init)
      if (!watching || response.url !== expectedUrl || response.redirected) return response
      const signal = init?.signal === undefined ? request?.signal : init.signal
      signal?.throwIfAborted()
      let selection: unknown
      if (response.ok) {
        const body: unknown = await response
          .clone()
          .json()
          .catch(() => undefined)
        signal?.throwIfAborted()
        if (body && typeof body === 'object' && !Array.isArray(body)) {
          // The browser binding needs only the public selection and the outstanding form.
          const keep = [
            'threadId',
            'runId',
            'runStatus',
            'activeRunId',
            'checkpointId',
            'latestPhysicalRunId',
            'interruptRunId',
            'physicalInterruptRunId',
            'interrupts',
          ]
          selection = Object.fromEntries(Object.entries(body).filter(([key]) => keep.includes(key)))
        }
      }
      if (watching) {
        const inspect = window.moldySelectedApprovalRead
        if (!inspect) throw new Error('The selected approval observer is missing')
        const decision = inspect(response.status, selection)
        if (!signal) await decision
        else {
          let onAbort: (() => void) | undefined
          try {
            await Promise.race([
              decision,
              new Promise<never>((_, reject) => {
                onAbort = () => reject(signal.reason)
                signal.addEventListener('abort', onAbort, { once: true })
                if (signal.aborted) onAbort()
              }),
            ])
          } finally {
            if (onAbort) signal.removeEventListener('abort', onAbort)
          }
        }
      }
      signal?.throwIfAborted()
      return response
    }
  }, snapshotUrl)
  return async () => {
    if (!page.isClosed()) await page.evaluate(() => window.moldyStopApprovalObserver?.())
  }
}
