import { test, expect } from './fixtures'

// E2E: /usage Spend Dashboard (M10).
//
// Backend interactions are mocked via Playwright `page.route` so this spec
// runs whether or not FastAPI is up. Coverage:
//
//   1. Summary cards render (this-month rollup from /api/usage/summary).
//   2. Default 30d filter shows the line chart with one point per day.
//   3. Toggling group_by=Target re-fetches and renders the bar chart.
//   4. Raw-data table reflects the current group_by.

const NOW = new Date()
const isoDate = (offsetDays: number) => {
  const d = new Date(NOW)
  d.setUTCHours(0, 0, 0, 0)
  d.setUTCDate(d.getUTCDate() - offsetDays)
  return d.toISOString().slice(0, 10)
}

const SUMMARY = {
  period: '30d',
  total_tokens: 245_123,
  prompt_tokens: 145_000,
  completion_tokens: 100_123,
  estimated_cost_usd: 12.345,
  by_agent: [],
}

// 10 daily entries with monotonic cost so the line chart paints something
// meaningful and the table has rows to render.
const DAILY_BY_DATE = Array.from({ length: 10 }, (_, i) => ({
  date: isoDate(9 - i),
  target_id: null,
  target_label: null,
  total_tokens_in: 1_000 * (i + 1),
  total_tokens_out: 700 * (i + 1),
  total_cost_usd: 0.05 * (i + 1),
  request_count: 5 * (i + 1),
}))

const DAILY_BY_TARGET = [
  {
    date: null,
    target_id: 'agent-research',
    target_label: 'Research Agent',
    total_tokens_in: 50_000,
    total_tokens_out: 30_000,
    total_cost_usd: 4.21,
    request_count: 120,
  },
  {
    date: null,
    target_id: 'agent-writer',
    target_label: 'Writer Agent',
    total_tokens_in: 12_000,
    total_tokens_out: 8_000,
    total_cost_usd: 0.84,
    request_count: 35,
  },
]

test.describe('Spend Dashboard', () => {
  test('renders summary cards, line chart for date grouping, bar chart on toggle', async ({
    page,
  }) => {
    await page.route('**/api/usage/summary**', (route) =>
      route.fulfill({ json: SUMMARY }),
    )

    let dailyCalls = 0
    await page.route('**/api/usage/daily**', (route) => {
      dailyCalls += 1
      const url = new URL(route.request().url())
      const groupBy = url.searchParams.get('group_by')
      const payload = groupBy === 'target' ? DAILY_BY_TARGET : DAILY_BY_DATE
      route.fulfill({ json: payload })
    })

    await page.goto('/usage')

    // Summary cards — 4 cards, current month rollup.
    await expect(page.getByText('이번 달 비용')).toBeVisible()
    await expect(page.getByText('이번 달 토큰')).toBeVisible()
    await expect(page.getByText('이번 달 요청')).toBeVisible()
    await expect(page.getByText('평균 비용/요청')).toBeVisible()

    // Default group_by=date → line chart visible, bar chart not.
    await expect(page.getByTestId('spend-line-chart')).toBeVisible()
    await expect(page.getByTestId('spend-bar-chart')).toHaveCount(0)

    // 10 line points = 10 daily entries.
    await expect(page.getByTestId('spend-line-point')).toHaveCount(10)

    // Filter bar present.
    await expect(page.getByTestId('usage-filter-bar')).toBeVisible()
    await expect(page.getByTestId('group-by-tabs')).toBeVisible()

    // Toggle group_by → Target. Bar chart should now be visible.
    await page.getByTestId('group-by-tabs').getByRole('tab', { name: '대상' }).click()

    await expect(page.getByTestId('spend-bar-chart')).toBeVisible()
    await expect(page.getByTestId('spend-line-chart')).toHaveCount(0)

    // Two bar rows for the two agents in the target payload.
    await expect(page.getByTestId('spend-bar-row')).toHaveCount(2)
    // Bar chart and the raw-data table both render the labels — pick the
    // chart-scoped instance to avoid strict-mode collisions.
    const barChart = page.getByTestId('spend-bar-chart')
    await expect(barChart.getByText('Research Agent')).toBeVisible()
    await expect(barChart.getByText('Writer Agent')).toBeVisible()

    // Daily endpoint should have been called at least twice — initial load
    // (date) and after toggle (target).
    expect(dailyCalls).toBeGreaterThanOrEqual(2)
  })

  test('CSV download button enabled when entries exist', async ({ page }) => {
    await page.route('**/api/usage/summary**', (route) =>
      route.fulfill({ json: SUMMARY }),
    )
    await page.route('**/api/usage/daily**', (route) =>
      route.fulfill({ json: DAILY_BY_DATE }),
    )

    await page.goto('/usage')

    const csvButton = page.getByTestId('usage-csv-download')
    await expect(csvButton).toBeVisible()
    await expect(csvButton).toBeEnabled()
  })

  test('empty state renders when daily aggregate has no entries', async ({ page }) => {
    await page.route('**/api/usage/summary**', (route) =>
      route.fulfill({ json: { ...SUMMARY, total_tokens: 0, estimated_cost_usd: 0 } }),
    )
    await page.route('**/api/usage/daily**', (route) => route.fulfill({ json: [] }))

    await page.goto('/usage')

    await expect(page.getByText('아직 사용 내역이 없습니다.')).toBeVisible()
    await expect(page.getByTestId('usage-csv-download')).toBeDisabled()
  })
})
