import type { Page, TestInfo } from '@playwright/test'

export async function recordFailureUiEvidence(
  page: Page,
  testInfo: Pick<TestInfo, 'annotations'>,
): Promise<void> {
  if (page.isClosed()) return
  // execution.json is scanned before export; disposable raw output is removed.
  const snapshot = await page
    .locator('body')
    .ariaSnapshot({ timeout: 2_000 })
    .catch(() => '')
  testInfo.annotations.push({
    type: 'moldy.failure-ui.v1',
    description: JSON.stringify({
      pathname: new URL(page.url()).pathname,
      snapshot: snapshot.slice(0, 12_000),
    }),
  })
}
