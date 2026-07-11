import { API_BASE, apiDeleteOk, apiGetJson, apiPostJson, isRecord, loginApi, test } from '../fixtures'
import { capture, DESKTOP_VIEWPORT, scriptedModelId, settle } from './_capture-helpers'
import { expect, type APIRequestContext, type Page } from '@playwright/test'
import type { CsrfHeaders } from '../fixtures'

/**
 * Phase 2 — 스킬 스튜디오 **기능별 전수 검증 투어** (20장).
 *
 * 화면 나열이 아니라 이번 릴리스의 사용자 플로우를 실 백엔드에서 하나씩
 * 조작·단언하며 캡처한다: 목록 표/검색/행 액션/벌크 삭제 실행, 연결 실카운트,
 * 소스 편집→리비전 생성, 버전 diff, read-only 뷰어, 평가/설정 탭, 게시 가드,
 * 스킬 스위처 탭 유지, 레거시 딥링크 redirect, 빌더 스코프/개선 진입/인덱스.
 * E2E_CAPTURE_TOUR=1 게이트, scripted system LLM 전제(E2E_LLM_* 비움).
 */

const WAVE = 'skill-studio'

const SKILL_BODY_V1 =
  '---\nname: meeting-actions\ndescription: "Use when extracting action items from meeting notes."\n---\n\n회의록에서 액션 아이템을 추출한다.\n'
const SKILL_BODY_V2 =
  '---\nname: meeting-actions\ndescription: "Use when extracting action items from meeting notes."\n---\n\n회의록에서 액션 아이템을 추출한다.\n마감일이 없으면 "미정"으로 표기한다.\n'
const SKILL_BODY_V3_LINE = '담당자 후보는 참석자 목록으로 제한한다.'

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

async function shot(page: Page, file: string): Promise<void> {
  await settle(page)
  await capture(page, WAVE, file)
}

test.describe('Skill studio captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize(DESKTOP_VIEWPORT)
  })

  test('feature-by-feature studio verification tour', async ({ page, request }) => {
    test.setTimeout(480_000)
    const csrfHeaders = await loginApi(request)
    const skillIds: string[] = []
    let agentId: string | null = null
    // 멱등성 — 직전 타임아웃 런의 finally는 실행되지 않으므로(slug unique 충돌
    // 방지) 같은 시드 잔존물을 먼저 정리한다.
    const TOUR_SLUGS = new Set([
      'meeting-actions',
      'weekly-report',
      'bulk-target-a',
      'bulk-target-b',
    ])
    const existingAgents = (await apiGetJson(request, `${API_BASE}/api/agents`)) as Array<
      Record<string, unknown>
    >
    for (const stale of existingAgents.filter((a) => a.name === '회의 비서')) {
      await request
        .delete(`${API_BASE}/api/agents/${stale.id}`, { headers: csrfHeaders })
        .catch(() => {})
    }
    const existingSkills = (await apiGetJson(request, `${API_BASE}/api/skills`)) as Array<
      Record<string, unknown>
    >
    for (const stale of existingSkills.filter((s) => TOUR_SLUGS.has(String(s.slug)))) {
      await apiDeleteOk(request, `${API_BASE}/api/skills/${stale.id}`, csrfHeaders)
    }
    try {
      // ── 시드: 스킬 4개(주 스킬 A는 리비전 2개) + A에 연결된 에이전트 1개 ──
      const primary = await seedSkill(request, csrfHeaders, {
        name: '회의록 액션 아이템',
        slug: 'meeting-actions',
        description: '회의록에서 담당자와 마감일을 정리합니다.',
        content: SKILL_BODY_V1,
      })
      skillIds.push(primary)
      const updated = await request.put(`${API_BASE}/api/skills/${primary}/content`, {
        headers: csrfHeaders,
        data: { content: SKILL_BODY_V2 },
      })
      expect(updated.ok()).toBeTruthy()
      const secondary = await seedSkill(request, csrfHeaders, {
        name: '주간 리포트 요약',
        slug: 'weekly-report',
        description: '주간 리포트를 한 페이지로 요약합니다.',
        content: SKILL_BODY_V1.replace('meeting-actions', 'weekly-report'),
      })
      skillIds.push(secondary)
      for (const [name, slug] of [
        ['벌크 삭제 대상 A', 'bulk-target-a'],
        ['벌크 삭제 대상 B', 'bulk-target-b'],
      ] as const) {
        skillIds.push(
          await seedSkill(request, csrfHeaders, {
            name,
            slug,
            description: '일괄 삭제 검증용 스킬입니다.',
            content: SKILL_BODY_V1.replace('meeting-actions', slug),
          }),
        )
      }
      // used_by_count 실집계 검증용 — A를 쓰는 에이전트.
      const modelId = await scriptedModelId(request)
      const agent = await apiPostJson(request, `${API_BASE}/api/agents`, csrfHeaders, {
        name: '회의 비서',
        description: '회의록을 정리하는 에이전트',
        system_prompt: '회의록에서 액션 아이템을 정리한다.',
        model_id: modelId,
        skill_ids: [primary],
      })
      if (isRecord(agent) && typeof agent.id === 'string') agentId = agent.id

      // ── 01. 목록 표 — 연결 실카운트(M1 역집계) ──────────────────────────
      await page.goto('/skills', { waitUntil: 'domcontentloaded', timeout: 90_000 })
      await expect(page.getByText('회의록 액션 아이템')).toBeVisible({ timeout: 30_000 })
      const primaryRow = page.getByRole('row').filter({ hasText: '회의록 액션 아이템' })
      await expect(primaryRow.getByText('1개 에이전트')).toBeVisible({ timeout: 15_000 })
      await shot(page, '01-list-table.png')

      // ── 02. 검색 필터 유지 ──────────────────────────────────────────────
      await page.getByPlaceholder('스킬 검색').fill('회의록')
      await expect(page.getByText('주간 리포트 요약')).toBeHidden({ timeout: 15_000 })
      await expect(page.getByText('회의록 액션 아이템')).toBeVisible()
      await shot(page, '02-list-search.png')
      await page.getByPlaceholder('스킬 검색').fill('')
      await expect(page.getByText('주간 리포트 요약')).toBeVisible({ timeout: 15_000 })

      // ── 03. 행 메뉴(소스/게시/내보내기 없음(text)/삭제) ────────────────
      await primaryRow.getByRole('button', { name: '회의록 액션 아이템 추가 작업' }).click()
      await expect(page.getByRole('menuitem', { name: '소스 보기' })).toBeVisible()
      await expect(page.getByRole('menuitem', { name: '공개하기' })).toBeVisible()
      await shot(page, '03-row-menu.png')
      await page.keyboard.press('Escape')

      // ── 04~06. 벌크 선택 → 확인(이름 열거) → 삭제 실행/리셋 ────────────
      for (const name of ['벌크 삭제 대상 A', '벌크 삭제 대상 B']) {
        await page
          .getByRole('row')
          .filter({ hasText: name })
          .getByRole('checkbox', { name: '행 선택' })
          .check()
      }
      await expect(page.getByTestId('skill-bulk-bar')).toContainText('2개 선택됨')
      await shot(page, '04-bulk-bar.png')

      await page.getByTestId('skill-bulk-bar').getByRole('button', { name: '삭제' }).click()
      const bulkDialog = page.getByRole('alertdialog')
      await expect(bulkDialog).toContainText('스킬 2개 삭제')
      await expect(bulkDialog).toContainText('벌크 삭제 대상 A')
      await expect(bulkDialog).toContainText('벌크 삭제 대상 B')
      await shot(page, '05-bulk-confirm.png')

      await bulkDialog.getByRole('button', { name: '삭제' }).click()
      await expect(page.getByText('스킬 2개를 삭제했습니다')).toBeVisible({ timeout: 20_000 })
      await expect(page.getByText('벌크 삭제 대상 A')).toBeHidden({ timeout: 15_000 })
      await expect(page.getByTestId('skill-bulk-bar')).toBeHidden()
      await shot(page, '06-bulk-deleted.png')

      // ── 07. 행 클릭 → 소스 탭 + 컨텍스트 바(연결 1) ─────────────────────
      await page.getByText('회의록 액션 아이템').click()
      await page.waitForURL(new RegExp(`/skills/${primary}/source`), { timeout: 30_000 })
      const contextBar = page.getByTestId('studio-context-bar')
      await expect(contextBar).toContainText('회의록 액션 아이템')
      await expect(contextBar).toContainText('연결 에이전트')
      await expect(contextBar).toContainText('1')
      await shot(page, '07-source-tab.png')

      // ── 08. 소스 직접 편집 → 저장(=리비전 생성, D2) ─────────────────────
      const editor = page.getByRole('textbox')
      await expect(editor).toHaveValue(/미정/, { timeout: 15_000 })
      await editor.fill(`${SKILL_BODY_V2}${SKILL_BODY_V3_LINE}\n`)
      await page.getByRole('button', { name: '저장' }).click()
      await expect(page.getByText('저장되었습니다')).toBeVisible({ timeout: 20_000 })
      await shot(page, '08-source-saved.png')

      // ── 09. 버전 탭 — 리비전 3개 + 방금 저장분 diff ─────────────────────
      await page.getByTestId('studio-tab-versions').click()
      await page.waitForURL(new RegExp(`/skills/${primary}/versions`))
      await expect(page.getByRole('heading', { name: '리비전 3', exact: true })).toBeVisible({
        timeout: 20_000,
      })
      const diffCard = page.getByTestId('revision-diff-card')
      await expect(diffCard).toContainText(`+ ${SKILL_BODY_V3_LINE}`, { timeout: 20_000 })
      await shot(page, '09-versions-diff.png')

      // ── 10. 이 버전 소스 보기 → read-only 뷰어 ──────────────────────────
      await diffCard.getByRole('link', { name: '이 버전 소스 보기' }).click()
      await page.waitForURL(/\/source\?revision=/)
      await expect(page.getByText('읽기 전용')).toBeVisible({ timeout: 20_000 })
      await expect(page.getByText(SKILL_BODY_V3_LINE)).toBeVisible()
      await expect(page.getByRole('button', { name: '파일 저장' })).toBeHidden()
      await shot(page, '10-revision-source.png')
      await page.getByRole('link', { name: '현재 버전 보기' }).click()
      await page.waitForURL(new RegExp(`/skills/${primary}/source$`))

      // ── 11. 평가 탭 (빈 세트 상태) ──────────────────────────────────────
      await page.getByTestId('studio-tab-evaluation').click()
      await page.waitForURL(new RegExp(`/skills/${primary}/evaluation`))
      await expect(page.getByText('아직 평가 세트가 없습니다')).toBeVisible({ timeout: 20_000 })
      await shot(page, '11-evaluation-tab.png')

      // ── 12~13. 설정 탭 + 삭제 확인(연결 경고, D1) ───────────────────────
      await page.getByTestId('studio-tab-settings').click()
      await page.waitForURL(new RegExp(`/skills/${primary}/settings`))
      await expect(page.getByText('메타데이터')).toBeVisible({ timeout: 20_000 })
      await expect(page.getByText('연결된 에이전트 1개')).toBeVisible()
      await shot(page, '12-settings-tab.png')

      await page.getByRole('button', { name: '스킬 삭제' }).click()
      const deleteDialog = page.getByRole('alertdialog')
      await expect(deleteDialog).toContainText('연결된 에이전트 1개')
      await shot(page, '13-settings-delete-confirm.png')
      await deleteDialog.getByRole('button', { name: '취소' }).click()

      // ── 14. 게시 진입(미게시 스킬 → 마법사 열림, canPublish 가드) ───────
      await page.getByRole('button', { name: '공개하기' }).click()
      await expect(page.getByRole('dialog')).toBeVisible({ timeout: 20_000 })
      await shot(page, '14-publish-wizard.png')
      await page.keyboard.press('Escape')

      // ── 15~16. 스킬 스위처 — 드롭다운 + 탭 유지 전환 ────────────────────
      await page.getByTestId('studio-skill-switcher').click()
      await expect(page.getByRole('menuitem', { name: /주간 리포트 요약/ })).toBeVisible()
      await shot(page, '15-switcher-open.png')
      await page.getByRole('menuitem', { name: /주간 리포트 요약/ }).click()
      await page.waitForURL(new RegExp(`/skills/${secondary}/settings`), { timeout: 30_000 })
      await expect(contextBar).toContainText('주간 리포트 요약')
      await shot(page, '16-switched-keeps-tab.png')

      // ── 17. 레거시 딥링크 redirect (M2b 안전망) ─────────────────────────
      await page.goto(`/skills?detailId=${primary}&tab=history`, {
        waitUntil: 'domcontentloaded',
      })
      await page.waitForURL(new RegExp(`/skills/${primary}/versions`), { timeout: 30_000 })
      await expect(page.getByRole('heading', { name: '리비전 3', exact: true })).toBeVisible({
        timeout: 20_000,
      })
      await shot(page, '17-legacy-redirect.png')

      // ── 18. 빌더 탭 — ?skillId= 스코프에서 컨텍스트 유지 (리뷰 R) ───────
      await page.getByTestId('studio-tab-builder').click()
      await page.waitForURL(new RegExp(`/skills/builder\\?skillId=${primary}`))
      await expect(contextBar).toContainText('회의록 액션 아이템')
      await expect(page.getByTestId('studio-tab-source')).toBeEnabled()
      await shot(page, '18-builder-scoped-index.png')

      // ── 19. 개선 시작 → 빌더 챗(시드 워크스페이스 + 자동 발화) ──────────
      await page.getByRole('button', { name: /회의록 액션 아이템 개선 시작/ }).click()
      await page.waitForURL(/\/skills\/builder\/[0-9a-f-]{36}/, { timeout: 60_000 })
      await expect(page.getByTestId('skill-builder-rail')).toBeVisible({ timeout: 60_000 })
      // improve 시드 — 원본 SKILL.md가 드래프트 파일 목록에 보인다.
      await expect(page.getByTestId('skill-builder-rail')).toContainText('SKILL.md', {
        timeout: 30_000,
      })
      // 자동 첫 발화에 대한 scripted 응답까지 확인(대화 가능 상태).
      await expect(page.locator('[data-moldy-message-id]').first()).toBeVisible({
        timeout: 60_000,
      })
      await shot(page, '19-improve-builder-chat.png')

      // ── 20. 빌더 인덱스 — 방금 세션이 이력에 반영(list invalidate) ──────
      await page.goto('/skills/builder', { waitUntil: 'domcontentloaded' })
      await expect(page.getByTestId('builder-session-list')).toContainText('개선', {
        timeout: 30_000,
      })
      await shot(page, '20-builder-index-history.png')
    } finally {
      if (agentId) {
        await request
          .delete(`${API_BASE}/api/agents/${agentId}`, { headers: csrfHeaders })
          .catch(() => {})
      }
      for (const id of skillIds) {
        await apiDeleteOk(request, `${API_BASE}/api/skills/${id}`, csrfHeaders)
      }
    }
  })
})
