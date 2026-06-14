import { mkdir } from 'node:fs/promises'
import path from 'node:path'

import { test, expect } from './fixtures'

const now = '2026-06-01T00:00:00.000Z'

const skill = {
  id: 'skill-history',
  name: 'Korea Weather',
  slug: 'korea-weather',
  description: '한국 날씨 응답을 안정적으로 정리합니다.',
  kind: 'package',
  version: '0.1.0',
  storage_path: null,
  content_hash: '333333333333',
  size_bytes: 2048,
  used_by_count: 1,
  package_metadata: null,
  health: {
    state: 'ready',
    label: '검증됨',
    reason: 'Latest evaluation passed for the current skill.',
    severity: 'success',
  },
  latest_evaluation_summary: {
    status: 'completed',
    latest_run_id: 'run-1',
    evaluation_set_id: 'set-1',
    pass_rate: 0.92,
    skill_content_hash: '333333333333',
    created_at: now,
    completed_at: '2026-06-01T00:01:00.000Z',
  },
  last_modified_at: now,
  created_at: now,
  updated_at: now,
  origin_summary: null,
  publication_summary: null,
  installation: null,
}

const revisions = [
  {
    id: 'rev-1',
    skill_id: 'skill-history',
    revision_number: 1,
    operation: 'create',
    skill_version: '0.1.0',
    content_hash: '111111111111',
    size_bytes: 512,
    file_count: 1,
    changelog_summary: '처음 생성',
    created_at: '2026-06-01T00:00:00.000Z',
  },
  {
    id: 'rev-3',
    skill_id: 'skill-history',
    revision_number: 3,
    operation: 'builder_improvement',
    skill_version: '0.1.0',
    content_hash: '333333333333',
    size_bytes: 2048,
    file_count: 3,
    changelog_summary: '날씨 요약 규칙 개선',
    created_at: '2026-06-03T00:00:00.000Z',
  },
  {
    id: 'rev-2',
    skill_id: 'skill-history',
    revision_number: 2,
    operation: 'manual_content_update',
    skill_version: '0.1.0',
    content_hash: '222222222222',
    size_bytes: 1024,
    file_count: 2,
    changelog_summary: '문구 수정',
    created_at: '2026-06-02T00:00:00.000Z',
  },
]

test.describe('Skill history tab', () => {
  test('shows revision history newest first', async ({ page }) => {
    await page.route('**/api/skills**', (route) => {
      const url = new URL(route.request().url())
      const method = route.request().method()
      const pathName = url.pathname

      if (method === 'GET' && pathName === '/api/skills') {
        return route.fulfill({ json: [skill] })
      }
      if (method === 'GET' && pathName === '/api/skills/skill-history') {
        return route.fulfill({ json: skill })
      }
      if (method === 'GET' && pathName === '/api/skills/skill-history/revisions') {
        return route.fulfill({ json: revisions })
      }

      return route.fulfill({ status: 404, json: { detail: pathName } })
    })

    await page.goto('/skills?detailId=skill-history&tab=history')
    await expect(page.getByRole('dialog', { name: /Korea Weather/ })).toBeVisible()
    await expect(page.getByText('리비전 3')).toBeVisible()
    await expect(page.getByText('현재 버전')).toBeVisible()
    await expect(page.getByText(/빌더 개선/)).toBeVisible()

    const captureDir = path.resolve(process.cwd(), '../output/e2e-captures/20260615-skill-history')
    await mkdir(captureDir, { recursive: true })
    await page.screenshot({
      path: path.join(captureDir, 'history-tab-revisions.png'),
      fullPage: false,
    })
  })
})
