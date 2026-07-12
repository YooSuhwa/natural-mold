import { API_BASE, apiDeleteOk, apiGetJson, apiPostJson, isRecord, loginApi, test } from '../fixtures'
import { capture, DESKTOP_VIEWPORT, settle } from './_capture-helpers'
import { expect, type APIRequestContext, type Page } from '@playwright/test'
import type { CsrfHeaders } from '../fixtures'

/**
 * Phase 3 — 실측 평가 지표 **기능별 검증 투어** (8장).
 *
 * 실 백엔드에서 평가 런을 두 버전에 걸쳐 실제로 실행(llm-2 scripted arm)하고,
 * A/B 벤치마크 실측 배지·런 실비용·버전별 통과율·히스토리 배지·케이스/스킬
 * 휴먼 피드백·실단가 estimate를 하나씩 조작·단언하며 캡처한다.
 *
 * 게이트: E2E_CAPTURE_TOUR=1 + **E2E_SKILL_EVALUATION_ENABLED=true**
 * (playwright webServer가 backend SKILL_EVALUATION_ENABLED로 전달).
 * scripted system LLM 전제(E2E_LLM_* 비움 + fresh throwaway DB).
 */

const WAVE = 'skill-studio-phase3'

const SKILL_SLUG = 'phase3-meeting-actions'
const SKILL_BODY_V1 =
  '---\nname: phase3-meeting-actions\ndescription: "Use when extracting action items from meeting notes."\n---\n\n회의록에서 액션 아이템을 추출해 표로 정리한다.\n'
const SKILL_BODY_V2 = `${SKILL_BODY_V1}마감일이 없으면 "미정"으로 표기한다.\n`

async function shot(page: Page, file: string): Promise<void> {
  await settle(page)
  await capture(page, WAVE, file)
}

async function createEvaluationRunAndWait(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  skillId: string,
  setId: string,
): Promise<void> {
  const run = await apiPostJson(
    request,
    `${API_BASE}/api/skills/${skillId}/evaluations/${setId}/runs`,
    csrfHeaders,
    {},
  )
  if (!isRecord(run) || typeof run.id !== 'string') {
    throw new Error('evaluation run create failed')
  }
  await expect
    .poll(
      async () => {
        const runs = (await apiGetJson(
          request,
          `${API_BASE}/api/skills/${skillId}/evaluations/${setId}/runs`,
        )) as Array<Record<string, unknown>>
        const row = runs.find((item) => item.id === run.id)
        return typeof row?.status === 'string' ? row.status : 'missing'
      },
      { timeout: 120_000, intervals: [1_000] },
    )
    .toBe('completed')
}

test.describe('Skill studio phase 3 captures', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize(DESKTOP_VIEWPORT)
  })

  test('measured evaluation metrics verification tour', async ({ page, request }) => {
    test.setTimeout(480_000)
    const csrfHeaders = await loginApi(request)
    const skillIds: string[] = []

    // 멱등성 — 직전 타임아웃 런의 잔존 시드를 정리.
    const existingSkills = (await apiGetJson(request, `${API_BASE}/api/skills`)) as Array<
      Record<string, unknown>
    >
    for (const stale of existingSkills.filter((s) => s.slug === SKILL_SLUG)) {
      await apiDeleteOk(request, `${API_BASE}/api/skills/${stale.id}`, csrfHeaders)
    }

    try {
      // ── 시드: 스킬 v1 → 평가 세트 → 실측 런 1 ──────────────────────────
      const created = await apiPostJson(request, `${API_BASE}/api/skills`, csrfHeaders, {
        name: 'Phase3 회의록 액션',
        slug: SKILL_SLUG,
        description: '회의록에서 담당자와 마감일을 정리합니다.',
        content: SKILL_BODY_V1,
        version: '1.0.0',
      })
      if (!isRecord(created) || typeof created.id !== 'string') throw new Error('seed failed')
      const skillId = created.id
      skillIds.push(skillId)

      const evalSet = await apiPostJson(
        request,
        `${API_BASE}/api/skills/${skillId}/evaluations`,
        csrfHeaders,
        {
          name: '실측 A/B 스모크',
          description: '스킬 유/무 실측 비교',
          evals: [
            { input: '회의록에서 담당자와 마감일을 뽑아줘', expected: '담당자/마감일 표' },
            { input: '이번 주 액션 아이템을 정리해줘', expected: '액션 아이템 표' },
          ],
        },
      )
      if (!isRecord(evalSet) || typeof evalSet.id !== 'string') throw new Error('set seed failed')
      const setId = evalSet.id

      await createEvaluationRunAndWait(request, csrfHeaders, skillId, setId)

      // ── 버전 올리고(1.1.0 + 본문 v2) 런 2 — 버전별 통과율 축 생성 ──────
      const contentUpdated = await request.put(`${API_BASE}/api/skills/${skillId}/content`, {
        headers: csrfHeaders,
        data: { content: SKILL_BODY_V2 },
      })
      expect(contentUpdated.ok()).toBeTruthy()
      const versionBumped = await request.patch(`${API_BASE}/api/skills/${skillId}`, {
        headers: csrfHeaders,
        data: { version: '1.1.0' },
      })
      expect(versionBumped.ok()).toBeTruthy()
      await createEvaluationRunAndWait(request, csrfHeaders, skillId, setId)

      // ── 01. 평가 탭 전경 — usage 카드 + 피드백 카드 + 버전 패널 ─────────
      await page.goto(`/skills/${skillId}/evaluation`, {
        waitUntil: 'domcontentloaded',
        timeout: 90_000,
      })
      const usageCard = page.getByTestId('skill-usage-summary-card')
      await expect(usageCard).toBeVisible({ timeout: 30_000 })
      // 실측 usage — 평가 런 2회가 스킬 축 원장에 적재됐다.
      await expect(usageCard).toContainText('평가 실행', { timeout: 15_000 })
      await expect(usageCard).toContainText('2')
      await expect(page.getByTestId('skill-feedback-card')).toBeVisible()
      await shot(page, '01-evaluation-overview.png')

      // ── 02. 버전별 통과율 — 1.0.0/1.1.0 두 축 ──────────────────────────
      const versionPanel = page.getByTestId('skill-version-pass-rate-panel')
      await expect(versionPanel).toContainText('1.0.0', { timeout: 15_000 })
      await expect(versionPanel).toContainText('1.1.0')
      await expect(versionPanel.getByTestId('skill-metric-bar')).toHaveCount(2)
      await shot(page, '02-version-pass-rates.png')

      // ── 03. A/B 벤치마크 — 실측 배지 + with/without 바 + 델타 ───────────
      const benchmark = page.getByTestId('skill-benchmark-panel')
      await expect(benchmark.getByTestId('benchmark-measured')).toBeVisible({ timeout: 15_000 })
      await expect(benchmark).toContainText('스킬 사용')
      await expect(benchmark).toContainText('스킬 없이')
      // scripted grader: with=pass(0.95), without=fail(0.3) — 양수 델타 실측.
      await expect(benchmark).toContainText('통과율 차이')
      await shot(page, '03-ab-benchmark.png')

      // ── 04. 런 실측 사용량 라인 — 모델 콜/토큰/비용 ─────────────────────
      const usageLine = page.getByTestId('run-usage-line')
      await expect(usageLine).toContainText('모델 콜', { timeout: 15_000 })
      await expect(usageLine).toContainText('토큰')
      await shot(page, '04-run-measured-usage.png')

      // ── 05. 케이스 피드백 — 판정 비동의 + 코멘트 저장 ───────────────────
      const disagree = page.getByTestId('case-feedback-disagree-0')
      await disagree.scrollIntoViewIfNeeded()
      await disagree.click()
      await expect
        .poll(
          async () => {
            const runs = (await apiGetJson(
              request,
              `${API_BASE}/api/skills/${skillId}/evaluations/${setId}/runs`,
            )) as Array<Record<string, unknown>>
            const latest = runs.find((row) => row.status === 'completed')
            if (!latest) return []
            const rows = (await apiGetJson(
              request,
              `${API_BASE}/api/skills/${skillId}/evaluations/${setId}/runs/${latest.id}/case-feedback`,
            )) as Array<Record<string, unknown>>
            return rows.map((row) => `${row.case_index}:${row.verdict}`)
          },
          { timeout: 20_000 },
        )
        .toContain('0:disagree')
      await shot(page, '05-case-feedback.png')

      // ── 06. 스킬 피드백 — 도움됨 + 카운트 반영 ──────────────────────────
      const upButton = page.getByTestId('skill-feedback-up')
      await upButton.scrollIntoViewIfNeeded()
      await upButton.click()
      await expect(upButton).toContainText('1', { timeout: 15_000 })
      await shot(page, '06-skill-feedback.png')

      // ── 07. estimate 다이얼로그 — 실단가 기반 예상 비용 + 실행 모델 ─────
      await page
        .getByRole('button', { name: /다시 실행/ })
        .first()
        .click()
      await expect(page.getByTestId('estimate-cost')).toBeVisible({ timeout: 20_000 })
      await shot(page, '07-estimate-dialog.png')
      await page.keyboard.press('Escape')

      // ── 08. 히스토리(버전) 탭 — 리비전 행 통과율 배지 ───────────────────
      await page.getByTestId('studio-tab-versions').click()
      await page.waitForURL(new RegExp(`/skills/${skillId}/versions`), { timeout: 30_000 })
      await expect(page.getByTestId('revision-pass-rate').first()).toBeVisible({
        timeout: 20_000,
      })
      await shot(page, '08-history-pass-rate-badges.png')
    } finally {
      for (const id of skillIds) {
        await apiDeleteOk(request, `${API_BASE}/api/skills/${id}`, csrfHeaders)
      }
    }
  })
})
