import { mkdir } from 'node:fs/promises'
import path from 'node:path'

import { test, expect, isRecord } from './fixtures'

const now = '2026-06-15T00:00:00.000Z'

const sourceSkill = {
  id: 'skill-conflict-source',
  name: '회의록 액션 아이템',
  slug: 'meeting-actions',
  description: '회의록에서 담당자와 마감일을 정리합니다.',
  kind: 'package',
  version: '1.0.0',
  storage_path: null,
  content_hash: 'hash-before',
  size_bytes: 1024,
  used_by_count: 0,
  package_metadata: null,
  current_revision_id: null,
  health: null,
  latest_evaluation_summary: null,
  last_modified_at: now,
  created_at: now,
  updated_at: now,
  origin_summary: null,
  publication_summary: null,
  installation: null,
}

function session(id: string) {
  return {
    id,
    user_id: 'user-1',
    user_request: '마감일 추출을 더 엄격하게 해줘',
    mode: 'improve',
    status: 'review',
    current_phase: 2,
    source_skill_id: sourceSkill.id,
    base_skill_version: '1.0.0',
    base_content_hash: 'hash-before',
    base_snapshot: {
      files: [{ path: 'SKILL.md', content: '기존 마감일 규칙', role: 'skill' }],
    },
    draft_package: {
      name: '회의록 액션 아이템',
      slug: 'meeting-actions',
      description: '회의록에서 담당자와 마감일을 더 엄격하게 정리합니다.',
      files: [
        {
          path: 'SKILL.md',
          content: '개선된 마감일 규칙',
          media_type: 'text/markdown',
          role: 'skill',
        },
      ],
      credential_requirements: [],
      execution_profile: {},
      validation_issues: [],
      compatibility_result: {
        targets: { openai_codex: { status: 'pass', issues: [] } },
        error_count: 0,
        warning_count: 0,
        info_count: 0,
      },
      changelog_draft: { summary: '마감일 규칙 보강' },
      evals: null,
      benchmark: null,
    },
    validation_result: { error_count: 0, warning_count: 0, issues: [] },
    compatibility_result: null,
    changelog_draft: null,
    eval_result: null,
    trigger_eval_result: null,
    finalized_skill_id: null,
    error_message: null,
    created_at: now,
    updated_at: now,
  }
}

test.describe('Skill Builder conflict handling', () => {
  test('shows a recoverable conflict state for stale improve sessions', async ({ page }) => {
    let startCalls = 0

    await page.route('**/api/skills**', (route) => {
      const url = new URL(route.request().url())
      const method = route.request().method()
      const pathName = url.pathname

      if (method === 'GET' && pathName === '/api/skills') {
        return route.fulfill({ json: [sourceSkill] })
      }
      if (method === 'GET' && pathName === `/api/skills/${sourceSkill.id}`) {
        return route.fulfill({ json: sourceSkill })
      }
      if (method === 'GET' && pathName === `/api/skills/${sourceSkill.id}/files`) {
        return route.fulfill({
          json: [{ path: 'SKILL.md', is_dir: false, size: 18, modified_at: now }],
        })
      }
      if (method === 'GET' && pathName === `/api/skills/${sourceSkill.id}/files/SKILL.md`) {
        return route.fulfill({
          contentType: 'text/markdown; charset=utf-8',
          body: '# 회의록 액션 아이템\n기존 마감일 규칙',
        })
      }

      return route.fulfill({ status: 404, json: { detail: pathName } })
    })

    await page.route('**/api/skill-builder**', (route) => {
      const url = new URL(route.request().url())
      const method = route.request().method()
      const pathName = url.pathname

      if (method === 'POST' && pathName === '/api/skill-builder') {
        startCalls += 1
        const body: unknown = route.request().postDataJSON()
        expect(isRecord(body)).toBeTruthy()
        if (isRecord(body)) {
          expect(body.mode).toBe('improve')
          expect(body.source_skill_id).toBe(sourceSkill.id)
        }
        return route.fulfill({ json: session(`session-conflict-${startCalls}`) })
      }
      if (method === 'POST' && pathName.endsWith('/confirm')) {
        return route.fulfill({
          status: 409,
          json: {
            error: {
              code: 'SKILL_BUILDER_SOURCE_CONFLICT',
              message: '개선 세션 시작 이후 스킬이 변경되었습니다',
            },
          },
        })
      }

      return route.fulfill({ status: 404, json: { detail: pathName } })
    })

    await page.goto(`/skills?detailId=${sourceSkill.id}`)
    await page.getByRole('button', { name: '대화로 개선' }).click()
    await page.getByLabel('요청').fill('마감일 추출을 더 엄격하게 해줘')
    await page.getByRole('button', { name: '개선안 만들기' }).click()
    await expect(page.getByText('SKILL.md').first()).toBeVisible()
    await page.getByRole('button', { name: '개선 적용' }).click()

    await expect(page.getByText('스킬이 변경되었습니다', { exact: true })).toBeVisible()
    await expect(
      page.getByText('이 개선 세션을 시작한 뒤 원본 스킬이 변경되었습니다.'),
    ).toBeVisible()
    await expect(page.getByRole('button', { name: '최신 기준으로 다시 만들기' })).toBeVisible()
    await expect(page.getByRole('button', { name: '세션 버리기' })).toBeVisible()

    const captureDir = path.resolve(process.cwd(), '../output/e2e-captures/20260615-skill-builder')
    await mkdir(captureDir, { recursive: true })
    await page.screenshot({
      path: path.join(captureDir, 'builder-conflict.png'),
      fullPage: false,
    })

    await page.getByRole('button', { name: '최신 기준으로 다시 만들기' }).click()
    await expect(page.getByText('스킬이 변경되었습니다', { exact: true })).toBeHidden()
    expect(startCalls).toBe(2)
  })
})
