import { test, expect } from './fixtures'

// E2E: Skills page — create text skill.

test.describe('Skills page', () => {
  test('user can create a text skill and see it in the table', async ({ page }) => {
    let skills: Array<Record<string, unknown>> = []
    await page.route(/\/api\/skills(\?.*)?$/, (route) => {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON() as Record<string, unknown>
        const created = {
          id: 'skill-1',
          name: body.name,
          slug: 'snippet',
          description: body.description ?? null,
          kind: 'text',
          version: null,
          storage_path: null,
          content_hash: 'abc',
          size_bytes: ((body.content as string) ?? '').length,
          used_by_count: 0,
          package_metadata: null,
          last_modified_at: new Date().toISOString(),
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }
        skills = [created]
        return route.fulfill({ status: 201, json: created })
      }
      return route.fulfill({ json: skills })
    })

    await page.goto('/skills')
    await page.getByRole('button', { name: /create first skill|new skill/i }).first().click()

    await page.getByLabel('Name').fill('Greeting snippet')
    await page.getByLabel(/Content/).fill('# Hello\nThis is a snippet.')

    await page.getByRole('button', { name: /^save$/i }).click()
    await expect(page.getByText(/skill created/i)).toBeVisible()
    await expect(page.getByText('Greeting snippet')).toBeVisible()
  })
})
