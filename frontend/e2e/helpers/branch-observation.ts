import type { Page, Response } from '@playwright/test'

export const branchStage = {
  startingTurn: 0,
  turnIdle: 1,
  regenerateClicked: 2,
  editClicked: 3,
  editorFilled: 4,
  saveClicked: 5,
  pickerVisible: 6,
  latestBranchVisible: 7,
  previousClicked: 8,
} as const

type Stage = (typeof branchStage)[keyof typeof branchStage]

function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/** Collect bounded protocol facts without initiating requests or retaining message content. */
export function observeBranch(page: Page, api: string) {
  const summary = {
    stage: branchStage.startingTurn as Stage,
    sendStatus: 0,
    editStatus: 0,
    regenerateStatus: 0,
    snapshotCount: 0,
    siblingCount: 0,
    selectedLeafListed: false,
    hasCheckpoint: false,
  }
  const pending = new Set<Promise<void>>()
  const apiOrigin = new URL(api).origin
  const observe = async (response: Response) => {
    const url = new URL(response.url())
    if (url.origin !== apiOrigin && url.origin !== new URL(page.url()).origin) return
    const scope = /^\/api\/conversations\/[^/]+\/ag-ui\/(runs|snapshot)$/.exec(url.pathname)
    if (!scope) return
    const request = response.request()
    if (scope[1] === 'runs' && request.method() === 'POST') {
      const body = object(request.postDataJSON())
      const operation = object(object(body.forwardedProps).moldy).operation
      if (operation === 'edit') summary.editStatus = response.status()
      else if (operation === 'regenerate') summary.regenerateStatus = response.status()
      else if (operation === undefined) summary.sendStatus = response.status()
    }
    if (scope[1] !== 'snapshot' || request.method() !== 'GET' || !response.ok()) return
    const snapshot = object(await response.json())
    summary.snapshotCount = Math.min(64, summary.snapshotCount + 1)
    const head = snapshot.checkpointId
    if (typeof head !== 'string' || !head) return
    summary.hasCheckpoint = true
    if (!Array.isArray(snapshot.messageLinks)) return
    for (const row of snapshot.messageLinks) {
      const siblings = object(row).siblingCheckpointIds
      if (Array.isArray(siblings)) {
        summary.siblingCount = Math.min(64, Math.max(summary.siblingCount, siblings.length))
        if (siblings.length > 1 && siblings.includes(head)) summary.selectedLeafListed = true
      }
    }
  }
  const listen = (response: Response) => {
    const work = observe(response).catch(() => {})
    pending.add(work)
    void work.finally(() => pending.delete(work))
  }
  page.on('response', listen)
  return {
    reached(stage: Stage) {
      summary.stage = stage
    },
    async finish() {
      page.off('response', listen)
      if (pending.size) {
        let deadline: NodeJS.Timeout | undefined
        try {
          await Promise.race([
            Promise.all(pending),
            new Promise<void>((resolve) => {
              deadline = setTimeout(resolve, 500)
            }),
          ])
        } finally {
          clearTimeout(deadline)
        }
      }
      return { ...summary }
    },
  }
}
