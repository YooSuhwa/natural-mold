import { createHash } from 'node:crypto'
import type { Page, Response } from '@playwright/test'

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const MAX_RESPONSE_OBSERVATIONS = 64
const OBSERVATION_SETTLE_TIMEOUT_MS = 1_000

function idDigest(value: string): string {
  return createHash('sha256').update(value).digest('hex')
}

export function observeSummaryResponses(page: Page, apiBase: string, conversationId: string) {
  const pending = new Set<Promise<void>>()
  const links: Array<{
    ok: boolean
    requestIds: string[]
    returned: Array<{ messageId: string; runId: string; runIdIsUuid: boolean }>
  }> = []
  const details: Array<{
    ok: boolean
    runId: string
    hasMetrics: boolean
    activityJsonLength: number | null
  }> = []
  const apiOrigin = new URL(apiBase).origin
  const conversationPath = `/api/conversations/${encodeURIComponent(conversationId)}`
  let observationsTruncated = false
  let parseFailedCount = 0

  const onResponse = (response: Response) => {
    const url = new URL(response.url())
    if (url.origin !== apiOrigin) return
    const linkMatch = url.pathname === `${conversationPath}/run-message-links`
    const detailPrefix = `${conversationPath}/runs/`
    const detailRunId = url.pathname.startsWith(detailPrefix)
      ? url.pathname.slice(detailPrefix.length)
      : null
    if (!linkMatch && (!detailRunId || detailRunId.includes('/'))) return

    const observation = response
      .json()
      .then((body: unknown) => {
        if (linkMatch) {
          const returned = Array.isArray(body)
            ? body.flatMap((row) =>
                typeof row === 'object' &&
                row !== null &&
                typeof row.message_id === 'string' &&
                typeof row.run_id === 'string'
                  ? [
                      {
                        messageId: idDigest(row.message_id),
                        runId: idDigest(row.run_id),
                        runIdIsUuid: UUID_PATTERN.test(row.run_id),
                      },
                    ]
                  : [],
              )
            : []
          if (links.length < MAX_RESPONSE_OBSERVATIONS) {
            links.push({
              ok: response.ok(),
              requestIds: url.searchParams.getAll('message_id').map(idDigest),
              returned,
            })
          } else {
            observationsTruncated = true
          }
        } else if (detailRunId) {
          if (details.length < MAX_RESPONSE_OBSERVATIONS) {
            const metrics =
              typeof body === 'object' && body !== null && 'metrics' in body ? body.metrics : null
            details.push({
              ok: response.ok(),
              runId: idDigest(decodeURIComponent(detailRunId)),
              hasMetrics: typeof metrics === 'object' && metrics !== null,
              activityJsonLength:
                typeof metrics === 'object' &&
                metrics !== null &&
                'activity_json' in metrics &&
                Array.isArray(metrics.activity_json)
                  ? metrics.activity_json.length
                  : null,
            })
          } else {
            observationsTruncated = true
          }
        }
      })
      .catch(() => {
        parseFailedCount += 1
      })
      .then(() => undefined)
    pending.add(observation)
    void observation.finally(() => pending.delete(observation))
  }
  page.on('response', onResponse)

  return async (targetMessageId: string | null, acceptedRunId: string | null) => {
    page.off('response', onResponse)
    let timeoutId: ReturnType<typeof setTimeout> | undefined
    const settled = await Promise.race([
      Promise.allSettled([...pending]).then(() => true),
      new Promise<false>((resolve) => {
        timeoutId = setTimeout(() => resolve(false), OBSERVATION_SETTLE_TIMEOUT_MS)
      }),
    ])
    if (timeoutId) clearTimeout(timeoutId)
    const targetDigest = targetMessageId ? idDigest(targetMessageId) : null
    const acceptedDigest = acceptedRunId ? idDigest(acceptedRunId) : null
    const matchingLinks = targetDigest
      ? links.filter((item) => item.requestIds.includes(targetDigest))
      : []
    const returnedLinks = matchingLinks.flatMap((item) => item.returned)
    const exactLinks = targetDigest
      ? returnedLinks.filter((item) => item.messageId === targetDigest)
      : []
    const runDetails = acceptedDigest ? details.filter((item) => item.runId === acceptedDigest) : []
    return {
      targetPublicMessageIdSha256: targetDigest,
      acceptedRunIdSha256: acceptedDigest,
      observationConfigured: true,
      observationSettled: settled,
      incompleteResponseCount: pending.size,
      parseFailedCount,
      observationsTruncated,
      linkRequestCountForTarget: matchingLinks.length,
      allLinkResponsesOk: matchingLinks.length > 0 && matchingLinks.every((item) => item.ok),
      linkBodyContainsExactTarget: exactLinks.length > 0,
      linkedRunIdIsUuid: exactLinks.length > 0 && exactLinks.every((item) => item.runIdIsUuid),
      returnedLinksCountForTarget: exactLinks.length,
      runDetailRequestCountForAcceptedId: runDetails.length,
      runDetailOk: runDetails.some((item) => item.ok),
      runDetailHasMetricsObject: runDetails.some((item) => item.hasMetrics),
      runDetailActivityJsonLength:
        runDetails.findLast((item) => item.activityJsonLength !== null)?.activityJsonLength ?? null,
    }
  }
}
