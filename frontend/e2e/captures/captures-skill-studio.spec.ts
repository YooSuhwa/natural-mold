import {
  API_BASE,
  apiDeleteOk,
  apiPostJson,
  isRecord,
  loginApi,
  test,
} from '../fixtures'
import { capture, DESKTOP_VIEWPORT, settle } from './_capture-helpers'
import { expect, type APIRequestContext, type Page } from '@playwright/test'
import type { CsrfHeaders } from '../fixtures'

/**
 * Phase 2 — 스킬 스튜디오 캡처 투어 (6탭 IA/목록 표/벌크/버전 diff/설정).
 * 실 백엔드에 텍스트 스킬 2개를 시드하고(콘텐츠 갱신으로 리비전 2개 생성)
 * 각 탭을 캡처한다. E2E_CAPTURE_TOUR=1 게이트.
 */

const WAVE = 'skill-studio'

const SKILL_BODY_V1 =
  '---\nname: meeting-actions\ndescription: "Use when extracting action items from meeting notes."\n---\n\n회의록에서 액션 아이템을 추출한다.\n'
const SKILL_BODY_V2 =
  '---\nname: meeting-actions\ndescription: "Use when extracting action items from meeting notes."\n---\n\n회의록에서 액션 아이템을 추출한다.\n마감일이 없으면 "미정"으로 표기한다.\n'

async function seedSkill(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  payload: { name: string; slug: string; description: string; content: string },
): Promise<string> {
  const created = await apiPostJson(request, `${API_BASE}/api/skills`, csrfHeaders, payload)
  if (!isRecord(created) || typeof created.id !== 'string') {
    throw new Error('skill seed failed')
  }
  return created.id
}

async function tourCapture(page: Page, url: string, file: string): Promise<void> {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90_000 })
  await settle(page)
  await capture(page, WAVE, file)
}

test.describe('Skill studio captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize(DESKTOP_VIEWPORT)
  })

  test('captures the studio tabs with seeded skills', async ({ page, request }) => {
    test.setTimeout(300_000)
    const csrfHeaders = await loginApi(request)
    const skillIds: string[] = []
    try {
      const primary = await seedSkill(request, csrfHeaders, {
        name: '회의록 액션 아이템',
        slug: 'meeting-actions',
        description: '회의록에서 담당자와 마감일을 정리합니다.',
        content: SKILL_BODY_V1,
      })
      skillIds.push(primary)
      // 두 번째 리비전 — 버전 탭 diff 캡처용.
      const updated = await request.put(`${API_BASE}/api/skills/${primary}/content`, {
        headers: csrfHeaders,
        data: { content: SKILL_BODY_V2 },
      })
      expect(updated.ok()).toBeTruthy()
      skillIds.push(
        await seedSkill(request, csrfHeaders, {
          name: '주간 리포트 요약',
          slug: 'weekly-report',
          description: '주간 리포트를 한 페이지로 요약합니다.',
          content: SKILL_BODY_V1.replace('meeting-actions', 'weekly-report'),
        }),
      )

      // 01 — 목록 표.
      await tourCapture(page, '/skills', '01-list-table.png')

      // 02 — 다중 선택 + 벌크 바 (삭제는 실행하지 않는다).
      await page.getByRole('checkbox', { name: '모든 행 선택' }).check()
      await expect(page.getByTestId('skill-bulk-bar')).toBeVisible()
      await settle(page)
      await capture(page, WAVE, '02-bulk-selection.png')
      await page.getByRole('button', { name: '선택 해제' }).click()

      // 03 — 소스 탭 (+컨텍스트 바).
      await tourCapture(page, `/skills/${primary}/source`, '03-source-tab.png')

      // 04 — 버전 탭 + SKILL.md diff.
      await page.goto(`/skills/${primary}/versions`, { waitUntil: 'domcontentloaded' })
      await expect(page.getByTestId('revision-diff-card')).toBeVisible({ timeout: 20_000 })
      await settle(page)
      await capture(page, WAVE, '04-versions-diff.png')

      // 05 — 리비전 read-only 소스 뷰어.
      await page
        .getByTestId('revision-diff-card')
        .getByRole('link', { name: '이 버전 소스 보기' })
        .click()
      await page.waitForURL(/\/source\?revision=/)
      await expect(page.getByText('읽기 전용')).toBeVisible({ timeout: 20_000 })
      await settle(page)
      await capture(page, WAVE, '05-revision-source.png')

      // 06 — 설정 탭.
      await tourCapture(page, `/skills/${primary}/settings`, '06-settings-tab.png')

      // 07 — 빌더 인덱스 (스킬 스코프).
      await tourCapture(page, `/skills/builder?skillId=${primary}`, '07-builder-index.png')
    } finally {
      for (const id of skillIds) {
        await apiDeleteOk(request, `${API_BASE}/api/skills/${id}`, csrfHeaders)
      }
    }
  })
})
