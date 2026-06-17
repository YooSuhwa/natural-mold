import { mkdir } from 'node:fs/promises'
import path from 'node:path'

import { test, expect } from './fixtures'

const now = '2026-06-01T00:00:00.000Z'

const publishedSummary = {
  state: 'published_private',
  item_id: 'item-1',
  visibility: 'private',
  status: 'published',
  is_listed: false,
  latest_version_id: 'version-1',
  version_number: 1,
  shared_user_count: 0,
}

const baseSkill = {
  id: 'skill-base',
  name: 'Base Weather',
  slug: 'base-weather',
  description: '한국 날씨 응답을 안정적으로 정리합니다.',
  kind: 'package',
  version: '0.1.0',
  storage_path: null,
  content_hash: 'hash-current',
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
    latest_run_id: 'run-ready',
    evaluation_set_id: 'set-ready',
    pass_rate: 0.92,
    skill_content_hash: 'hash-current',
    created_at: now,
    completed_at: '2026-06-01T00:01:00.000Z',
  },
  last_modified_at: now,
  created_at: now,
  updated_at: now,
  origin_summary: null,
  publication_summary: publishedSummary,
  installation: null,
}

const skills = [
  {
    ...baseSkill,
    id: 'skill-needs-credentials',
    name: 'Credential Setup',
    health: {
      state: 'needs_credentials',
      label: '자격증명 필요',
      reason: '필수 자격증명이 없습니다.',
      severity: 'warning',
    },
  },
  {
    ...baseSkill,
    id: 'skill-needs-rerun',
    name: 'Rerun Needed',
    health: {
      state: 'needs_rerun',
      label: '재평가 필요',
      reason: '콘텐츠가 바뀌었습니다.',
      severity: 'warning',
    },
  },
  {
    ...baseSkill,
    id: 'skill-failed',
    name: 'Failed Eval',
    health: {
      state: 'evaluation_failed',
      label: '평가 실패',
      reason: '마지막 평가가 실패했습니다.',
      severity: 'error',
    },
  },
  {
    ...baseSkill,
    id: 'skill-local',
    name: 'Local Draft',
    publication_summary: {
      state: 'not_published',
      is_listed: false,
      shared_user_count: 0,
    },
  },
]

test.describe('Skill state filters', () => {
  test('filters installed skills by quality and publication state chips', async ({ page }) => {
    await page.route('**/api/skills**', (route) => {
      const url = new URL(route.request().url())
      const method = route.request().method()

      if (method === 'GET' && url.pathname === '/api/skills') {
        return route.fulfill({ json: skills })
      }

      return route.fulfill({ status: 404, json: { detail: url.pathname } })
    })

    await page.goto('/skills')

    await expect(page.getByRole('group', { name: '스킬 상태 필터' })).toBeVisible()
    await expect(page.getByRole('button', { name: '자격증명 필요 1개' })).toBeVisible()
    await expect(page.getByRole('button', { name: '재평가 필요 1개' })).toBeVisible()
    await expect(page.getByRole('button', { name: '평가 실패 1개' })).toBeVisible()
    await expect(page.getByRole('button', { name: '공개됨 3개' })).toBeVisible()
    await expect(page.getByRole('button', { name: '로컬/초안 1개' })).toBeVisible()

    await page.getByRole('button', { name: '자격증명 필요 1개' }).click()

    await expect(page.getByText('Credential Setup')).toBeVisible()
    await expect(page.getByText('Rerun Needed')).toBeHidden()
    await expect(page.getByText('Failed Eval')).toBeHidden()
    await expect(page.getByText('Local Draft')).toBeHidden()

    await page.getByRole('button', { name: '로컬/초안 1개' }).click()

    await expect(page.getByText('Local Draft')).toBeVisible()
    await expect(page.getByText('Credential Setup')).toBeHidden()

    const captureDir = path.resolve(
      process.cwd(),
      '../output/e2e-captures/20260615-skill-state-filters',
    )
    await mkdir(captureDir, { recursive: true })
    await page.screenshot({
      path: path.join(captureDir, 'skill-state-filter-chips.png'),
      fullPage: false,
    })
  })
})
