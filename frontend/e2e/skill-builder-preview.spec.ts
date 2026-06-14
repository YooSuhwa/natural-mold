import { mkdir } from 'node:fs/promises'
import path from 'node:path'

import { test, expect, isRecord } from './fixtures'

const now = '2026-06-15T00:00:00.000Z'

const sourceSkill = {
  id: 'skill-builder-source',
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

const improvedSession = {
  id: 'session-preview',
  user_id: 'user-1',
  user_request: '마감일 추출을 더 엄격하게 해줘',
  mode: 'improve',
  status: 'review',
  current_phase: 2,
  source_skill_id: sourceSkill.id,
  base_skill_version: '1.0.0',
  base_content_hash: 'hash-before',
  base_snapshot: {
    files: [
      { path: 'SKILL.md', content: '기존 마감일 규칙', role: 'skill' },
      { path: 'references/old.md', content: '이전 참고자료', role: 'reference' },
    ],
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
      {
        path: 'scripts/extract_due_dates.py',
        content: 'print("ok")',
        media_type: 'text/x-python',
        role: 'script',
      },
    ],
    credential_requirements: [],
    execution_profile: {},
    validation_issues: [],
    compatibility_result: {
      targets: {
        openai_codex: { status: 'pass', issues: [] },
      },
      error_count: 0,
      warning_count: 0,
      info_count: 0,
    },
    changelog_draft: {
      summary: '마감일 규칙 보강',
      items: [{ title: '날짜 표현을 더 엄격하게 처리', path: 'SKILL.md' }],
    },
    evals: null,
    benchmark: { pass_rate: 0.86, mean_score: 0.82, delta: 0.12 },
  },
  validation_result: {
    error_count: 1,
    warning_count: 1,
    info_count: 1,
    issues: [
      {
        severity: 'error',
        path: 'SKILL.md',
        message: '필수 frontmatter가 없습니다.',
      },
      {
        severity: 'warning',
        path: 'scripts/extract_due_dates.py',
        message: '네트워크 사용 선언이 필요합니다.',
      },
      {
        severity: 'info',
        message: '평가 케이스가 생성되었습니다.',
      },
    ],
  },
  compatibility_result: null,
  changelog_draft: null,
  eval_result: null,
  trigger_eval_result: null,
  finalized_skill_id: null,
  error_message: null,
  created_at: now,
  updated_at: now,
}

test.describe('Skill Builder preview', () => {
  test('shows improve review details before applying', async ({ page }) => {
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
          json: [
            { path: 'SKILL.md', is_dir: false, size: 18, modified_at: now },
            { path: 'references/old.md', is_dir: false, size: 18, modified_at: now },
          ],
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

    await page.route('**/api/skill-builder', (route) => {
      const body: unknown = route.request().postDataJSON()
      expect(isRecord(body)).toBeTruthy()
      if (isRecord(body)) {
        expect(body.mode).toBe('improve')
        expect(body.source_skill_id).toBe(sourceSkill.id)
      }
      return route.fulfill({ json: improvedSession })
    })

    await page.goto(`/skills?detailId=${sourceSkill.id}`)
    await expect(page.getByRole('dialog', { name: /회의록 액션 아이템/ })).toBeVisible()
    await page.getByRole('button', { name: '대화로 개선' }).click()
    await page.getByLabel('요청').fill('마감일 추출을 더 엄격하게 해줘')
    await page.getByRole('button', { name: '개선안 만들기' }).click()

    await expect(page.getByText('파일 변경 요약')).toBeVisible()
    await expect(page.getByText('원본 2개')).toBeVisible()
    await expect(page.getByText('개선안 2개')).toBeVisible()
    await expect(page.getByText('SKILL.md · 수정')).toBeVisible()
    await expect(page.getByText('scripts/extract_due_dates.py · 추가')).toBeVisible()
    await expect(page.getByText('references/old.md · 삭제')).toBeVisible()
    await expect(page.getByText('SKILL.md: 필수 frontmatter가 없습니다.')).toBeVisible()
    await expect(page.getByText('마감일 규칙 보강')).toBeVisible()
    await expect(page.getByText('SKILL.md: 날짜 표현을 더 엄격하게 처리')).toBeVisible()
    await expect(page.getByText('통과율 86%')).toBeVisible()
    await expect(page.getByText('평균 점수 0.82')).toBeVisible()
    await expect(page.getByText('변화 +0.12')).toBeVisible()

    const captureDir = path.resolve(process.cwd(), '../output/e2e-captures/20260615-skill-builder')
    await mkdir(captureDir, { recursive: true })
    await page.screenshot({
      path: path.join(captureDir, 'builder-preview-improve.png'),
      fullPage: false,
    })
    await page.getByText('통과율 86%').scrollIntoViewIfNeeded()
    await page.screenshot({
      path: path.join(captureDir, 'builder-preview-improve-details.png'),
      fullPage: false,
    })
  })
})
