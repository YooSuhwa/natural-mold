import { mkdir } from 'node:fs/promises'
import path from 'node:path'

import { test, expect } from './fixtures'

const now = '2026-06-15T00:00:00.000Z'

const packageSkill = {
  id: 'skill-export',
  name: 'Portable Export',
  slug: 'portable-export',
  description: '포터블 .skill 다운로드를 검증하는 패키지 스킬입니다.',
  kind: 'package',
  version: '1.0.0',
  storage_path: null,
  content_hash: 'hash-export',
  size_bytes: 2048,
  used_by_count: 0,
  package_metadata: null,
  current_revision_id: null,
  credential_requirements: null,
  execution_profile: null,
  health: null,
  latest_evaluation_summary: null,
  last_modified_at: now,
  created_at: now,
  updated_at: now,
  origin_summary: null,
  publication_summary: null,
  installation: null,
}

test.describe('Skill package export', () => {
  test('downloads the portable skill archive from the package detail footer', async ({ page }) => {
    const exportCalls: string[] = []

    await page.route('**/api/agents/summary', (route) => route.fulfill({ json: [] }))
    await page.route('**/api/skills**', (route) => {
      const url = new URL(route.request().url())
      const method = route.request().method()
      const pathName = url.pathname

      if (method === 'GET' && pathName === '/api/skills') {
        return route.fulfill({ json: [packageSkill] })
      }
      if (method === 'GET' && pathName === `/api/skills/${packageSkill.id}`) {
        return route.fulfill({ json: packageSkill })
      }
      if (method === 'GET' && pathName === `/api/skills/${packageSkill.id}/files`) {
        return route.fulfill({
          json: [
            { path: 'SKILL.md', is_dir: false, size: 128, modified_at: now },
            { path: 'scripts/run.py', is_dir: false, size: 32, modified_at: now },
          ],
        })
      }
      if (method === 'GET' && pathName === `/api/skills/${packageSkill.id}/files/SKILL.md`) {
        return route.fulfill({
          contentType: 'text/markdown; charset=utf-8',
          body:
            '---\nname: portable-export\ndescription: Export package test.\n---\n\nUse when testing export.',
        })
      }
      if (method === 'GET' && pathName === `/api/skills/${packageSkill.id}/export`) {
        exportCalls.push(url.searchParams.get('include_evals') === 'true' ? 'with-evals' : 'default')
        return route.fulfill({
          contentType: 'application/zip',
          headers: {
            'Content-Disposition': 'attachment; filename="portable-export.skill"',
          },
          body: 'PK\u0003\u0004portable-export',
        })
      }

      return route.fulfill({ status: 404, json: { detail: pathName } })
    })

    // Phase 2 스튜디오 — 내보내기는 설정 탭이 소유한다 (D1).
    await page.goto(`/skills/${packageSkill.id}/settings`)
    await expect(page.getByTestId('studio-context-bar')).toContainText('Portable Export')

    const exportAction = page.getByRole('button', { name: '.skill 내보내기' })
    await expect(exportAction).toHaveAttribute(
      'href',
      new RegExp(`/api/skills/${packageSkill.id}/export$`),
    )
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      exportAction.click(),
    ])

    expect(download.suggestedFilename()).toBe('portable-export.skill')
    expect(exportCalls).toEqual(['default'])

    const captureDir = path.resolve(process.cwd(), '../output/e2e-captures/20260615-skill-export')
    await mkdir(captureDir, { recursive: true })
    await page.screenshot({
      path: path.join(captureDir, 'package-export-footer.png'),
      fullPage: false,
    })
  })
})
