import type { Locator, Page, TestInfo } from '@playwright/test'
import { expect } from '../fixtures'

interface CaptureResourcePageInput {
  readonly page: Page
  readonly testInfo: TestInfo
  readonly state: string
  readonly evidence: Locator
  readonly surface?: Locator
  readonly horizontalBoundary?: Locator
  readonly verticalContainment?: {
    readonly target: Locator
    readonly boundary: Locator
  }
  readonly verify?: (width: number) => Promise<void>
}

/** Keep image output in the runner's screenshot-exporting project only. */
export async function captureResourcePage({
  page,
  testInfo,
  state,
  evidence,
  surface,
  horizontalBoundary,
  verticalContainment,
  verify,
}: CaptureResourcePageInput): Promise<void> {
  if (testInfo.project.name !== 'scripted-capture') return
  const originalViewport = page.viewportSize()
  for (const width of [375, 768, 1280]) {
    await page.setViewportSize({ width, height: 960 })
    await evidence.scrollIntoViewIfNeeded()
    await expect(evidence).toBeInViewport()
    await expect
      .poll(() =>
        page.evaluate(
          () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
        ),
      )
      .toBeLessThanOrEqual(1)
    if (surface) {
      await expect
        .poll(async () => {
          const box = await surface.boundingBox()
          return box ? box.x >= -1 && box.x + box.width <= width + 1 : false
        })
        .toBe(true)
      await expect
        .poll(() => surface.evaluate((element) => element.scrollWidth - element.clientWidth))
        .toBeLessThanOrEqual(1)
    }
    if (horizontalBoundary) {
      await expect
        .poll(() =>
          horizontalBoundary.evaluate((element) =>
            Math.max(element.scrollWidth - element.clientWidth, Math.abs(element.scrollLeft)),
          ),
        )
        .toBeLessThanOrEqual(1)
    }
    if (verticalContainment && width >= 768) {
      await expect
        .poll(async () => {
          const [targetBox, boundaryBox] = await Promise.all([
            verticalContainment.target.boundingBox(),
            verticalContainment.boundary.boundingBox(),
          ])
          if (!targetBox || !boundaryBox) return false
          return targetBox.y + targetBox.height <= boundaryBox.y + boundaryBox.height + 1
        })
        .toBe(true)
    }
    await verify?.(width)
    await page.screenshot({ path: testInfo.outputPath(`${state}-${width}.png`) })
  }
  if (originalViewport) await page.setViewportSize(originalViewport)
}
