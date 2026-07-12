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
  current_revision_id: 'rev-3',
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

const revisionOne = {
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
}

const revisionThree = {
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
}

const revisionTwo = {
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
}

const revisions = [revisionOne, revisionThree, revisionTwo]

const revisionDetails = {
  'rev-1': {
    ...revisionOne,
    parent_revision_id: null,
    changed_files: [],
    changelog_items: [],
    compatibility_result: null,
    evaluation_summary: null,
    metadata_json: {},
  },
  'rev-2': {
    ...revisionTwo,
    parent_revision_id: 'rev-1',
    changed_files: [{ path: 'SKILL.md', status: 'modified' }],
    changelog_items: [{ title: '날씨 응답 톤 수정', path: 'SKILL.md' }],
    compatibility_result: { targets: { openai_codex: { status: 'ok' } } },
    evaluation_summary: { status: 'completed', mean_score: 0.88 },
    metadata_json: {},
  },
  'rev-3': {
    ...revisionThree,
    parent_revision_id: 'rev-2',
    changed_files: [{ path: 'references/weather.md', status: 'added' }],
    changelog_items: [{ title: '지역별 요약 규칙 추가', path: 'references/weather.md' }],
    compatibility_result: { targets: { openai_codex: { status: 'ok' } } },
    evaluation_summary: { status: 'completed', mean_score: 0.92 },
    metadata_json: {},
  },
}

// M4 — 리비전 스냅샷 파일 API(diff/read-only 소스) 픽스처.
const revisionSkillMd: Record<string, string> = {
  'rev-1': '---\nname: weather\n---\n\n요약 규칙 v1\n',
  'rev-2': '---\nname: weather\n---\n\n요약 규칙 v2\n',
  'rev-3': '---\nname: weather\n---\n\n요약 규칙 v3\n지역별 요약 추가\n',
}

function getRevisionDetail(revisionId: string) {
  switch (revisionId) {
    case 'rev-1':
      return revisionDetails['rev-1']
    case 'rev-2':
      return revisionDetails['rev-2']
    case 'rev-3':
      return revisionDetails['rev-3']
    default:
      return null
  }
}

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
      // M4 — 리비전 파일 목록/내용 (detail 매처보다 먼저: 경로 prefix가 겹친다).
      const filesMatch = pathName.match(
        /^\/api\/skills\/skill-history\/revisions\/(rev-\d)\/files$/,
      )
      if (method === 'GET' && filesMatch) {
        const body = revisionSkillMd[filesMatch[1]]
        return route.fulfill({
          json: {
            snapshot_pruned: false,
            files: [{ path: 'SKILL.md', size: body?.length ?? 0, is_binary: false }],
          },
        })
      }
      const contentMatch = pathName.match(
        /^\/api\/skills\/skill-history\/revisions\/(rev-\d)\/files\/content$/,
      )
      if (method === 'GET' && contentMatch) {
        const body = revisionSkillMd[contentMatch[1]]
        if (body && url.searchParams.get('path') === 'SKILL.md') {
          return route.fulfill({ json: { path: 'SKILL.md', content: body } })
        }
        return route.fulfill({ status: 404, json: { detail: 'file not found' } })
      }
      if (method === 'GET' && pathName.startsWith('/api/skills/skill-history/revisions/')) {
        const revisionId = pathName.split('/').at(-1) ?? ''
        const detail = getRevisionDetail(revisionId)
        if (detail) {
          return route.fulfill({ json: detail })
        }
      }

      return route.fulfill({ status: 404, json: { detail: pathName } })
    })

    // Phase 2 스튜디오 — 레거시 딥링크가 버전 탭 라우트로 redirect되는 것까지 검증.
    await page.goto('/skills?detailId=skill-history&tab=history')
    await page.waitForURL(/\/skills\/skill-history\/versions/)
    await expect(page.getByTestId('studio-context-bar')).toContainText('Korea Weather')
    await expect(page.getByRole('heading', { name: '리비전 3', exact: true })).toBeVisible()
    await expect(page.getByText('현재 버전', { exact: true }).first()).toBeVisible()
    await expect(page.getByText(/빌더 개선/)).toBeVisible()
    await expect(page.getByText('리비전 3 상세')).toBeVisible()
    await expect(page.getByRole('button', { name: '리비전 3 되돌리기' })).toBeDisabled()

    await page.getByRole('button', { name: '리비전 2 보기' }).click()
    await expect(page.getByText('리비전 2 상세')).toBeVisible()
    await expect(page.getByText('날씨 응답 톤 수정 · SKILL.md')).toBeVisible()
    await expect(page.getByText('공용 호환성')).toBeVisible()
    await expect(page.getByText('OpenAI/Codex')).toBeVisible()
    // 스튜디오 컨텍스트 바의 "통과율 N%"와 substring 충돌 — exact 매칭.
    await expect(page.getByText('통과', { exact: true })).toBeVisible()

    // ── M4: SKILL.md diff (rev-2 vs parent rev-1) ──────────────────────
    const diffCard = page.getByTestId('revision-diff-card')
    await expect(diffCard).toBeVisible()
    await expect(diffCard).toContainText('- 요약 규칙 v1')
    await expect(diffCard).toContainText('+ 요약 규칙 v2')

    // ── M4: 이 버전 소스 보기 → read-only 리비전 뷰어 ──────────────────
    await diffCard.getByRole('link', { name: '이 버전 소스 보기' }).click()
    await page.waitForURL(/\/skills\/skill-history\/source\?revision=rev-2/)
    await expect(page.getByText('리비전 2 소스')).toBeVisible()
    await expect(page.getByText('읽기 전용')).toBeVisible()
    await expect(page.getByText('요약 규칙 v2')).toBeVisible()
    // 편집 UI가 없어야 한다 (read-only 계약).
    await expect(page.getByRole('button', { name: '파일 저장' })).toBeHidden()

    const captureDir = path.resolve(process.cwd(), '../output/e2e-captures/20260615-skill-history')
    await mkdir(captureDir, { recursive: true })
    await page.screenshot({
      path: path.join(captureDir, 'history-tab-detail.png'),
      fullPage: false,
    })
  })
})
