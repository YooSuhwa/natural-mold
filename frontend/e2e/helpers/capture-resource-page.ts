import type { Locator, Page, TestInfo } from '@playwright/test'
import { expect } from '../fixtures'

/** Keep image output in the runner's screenshot-exporting project only. */
export async function captureResourcePage(
  page: Page,
  testInfo: TestInfo,
  state: string,
  evidence: Locator,
): Promise<void> {
  if (testInfo.project.name !== 'scripted-capture') return
  const originalViewport = page.viewportSize()
  for (const width of [375, 768, 1280]) {
    await page.setViewportSize({ width, height: 960 })
    await evidence.scrollIntoViewIfNeeded()
    await expect(evidence).toBeInViewport()
    await page.screenshot({ path: testInfo.outputPath(`${state}-${width}.png`) })
  }
  if (originalViewport) await page.setViewportSize(originalViewport)
}
