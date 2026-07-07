import { test, expect, loginApi } from '../fixtures'
import { capture, DESKTOP_VIEWPORT, settle, warmUpChatRoute } from './_capture-helpers'

/**
 * 스킬 빌더 챗 캡처 투어 (skill-studio phase 1, 스펙 §2 시각 증빙).
 *
 * UI 진입부터 전 플로우를 실제 화면으로 남긴다:
 * 진입(스킬 목록 → 대화로 만들기) → 빌더 라우트 → 점진 편집 → 검증 레일 →
 * 시험 승인 카드+세션 동의 → 동의 후 무카드 재실행 → finalize 카드 → 완료
 * 배너/딥링크 → 리로드 replay → 생성 스킬 상세 → 대화로 개선(improve 시드) →
 * 세션 unavailable 상태. E2E_CAPTURE_TOUR=1 게이트 (일반 실행에선 skip).
 *
 * 전제: throwaway 스택 + scripted system LLM (E2E_LLM_* 비움 — CHECKPOINT M6 참조).
 */

const WAVE = 'skill-builder-chat'

test.describe('스킬 빌더 챗 — 전 화면 캡처 투어', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(300_000)
    await warmUpChatRoute(browser)
  })

  test('진입 → 편집 → 검증 → 동의 → finalize → 개선 전체 투어', async ({ page, request }) => {
    test.setTimeout(600_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)
    await loginApi(request)

    const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()

    // 런 직후 Enter가 무시될 수 있어 전송 버튼 폴백 (skill-builder-chat.spec 계약).
    const sendMessage = async (text: string) => {
      await composer.fill(text)
      await composer.press('Enter')
      try {
        await expect(composer).toHaveValue('', { timeout: 3_000 })
      } catch {
        await page.getByRole('button', { name: '전송' }).last().click()
        await expect(composer).toHaveValue('', { timeout: 10_000 })
      }
    }
    const approveWithRetry = async () => {
      await page.getByTestId('approval-approve-button').last().click()
      const retryPrompt = page
        .getByText('승인 응답을 전송하지 못했습니다. 다시 시도하세요.')
        .last()
      try {
        await expect(retryPrompt).toBeVisible({ timeout: 5_000 })
        await page.getByTestId('approval-approve-button').last().click()
      } catch {
        // 첫 승인 전송이 수락됨.
      }
    }

    // ── 1. 진입점: 스킬 목록 ────────────────────────────────────────────
    await page.goto('/skills', { waitUntil: 'domcontentloaded', timeout: 120_000 })
    await expect(page.getByRole('button', { name: '대화로 만들기' }).first()).toBeVisible({
      timeout: 60_000,
    })
    await settle(page)
    await capture(page, WAVE, '01-skills-entry.png')

    // ── 2. 대화로 만들기 다이얼로그 (chat 탭) ──────────────────────────
    await page.getByRole('button', { name: '대화로 만들기' }).first().click()
    const requestBox = page.locator('#skill-chat-request')
    await expect(requestBox).toBeVisible({ timeout: 15_000 })
    await requestBox.fill('회의록에서 담당자, 할 일, 마감일을 표로 정리하는 스킬을 만들어줘')
    await settle(page, 300)
    await capture(page, WAVE, '02-create-dialog-chat.png')

    // ── 3. 대화 시작 → 빌더 라우트 리다이렉트 ──────────────────────────
    await page.getByRole('button', { name: '대화 시작' }).click()
    await page.waitForURL(/\/skills\/builder\/[0-9a-f-]{36}/, { timeout: 120_000 })
    const sessionId = new URL(page.url()).pathname.split('/').pop() as string
    await expect(page.getByTestId('skill-builder-rail')).toBeVisible({ timeout: 60_000 })
    await expect(composer).toBeVisible({ timeout: 60_000 })
    await settle(page)
    await capture(page, WAVE, '03-builder-entry.png')

    // ── 3b. try-hint 클릭 → 컴포저 프리필 (M7 목업 차용) ────────────────
    await page.getByTestId('builder-try-hint').click()
    await expect(composer).not.toHaveValue('')
    await settle(page, 300)
    await capture(page, WAVE, '03b-try-hint-prefill.png')

    // ── 4. 점진 편집 (write_file — 승인 카드 없음, AD-3) ────────────────
    await sendMessage(`E2E_SKILL_BUILDER_WRITE /skill-drafts/${sessionId}`)
    await expect(page.getByText('드래프트 파일을 작성했습니다').last()).toBeVisible({
      timeout: 45_000,
    })
    await settle(page)
    await capture(page, WAVE, '04-draft-written.png')

    // ── 5. 검증 + 레일 (moldy.skill_draft/skill_validation) ────────────
    await sendMessage('E2E_SKILL_BUILDER_VALIDATE')
    await expect(page.getByText('드래프트 검증을 실행했습니다').last()).toBeVisible({
      timeout: 45_000,
    })
    const rail = page.getByTestId('skill-builder-rail')
    await expect(rail.getByTestId('builder-draft-files')).toBeVisible({ timeout: 30_000 })
    // M7 상태 카드 — 검증 행 + 런타임 호환 칩이 실데이터로 채워진다.
    await expect(rail.getByTestId('builder-status-rows')).toBeVisible({ timeout: 15_000 })
    await expect(rail.getByTestId('builder-runtime-chips')).toBeVisible({ timeout: 15_000 })
    await settle(page)
    await capture(page, WAVE, '05-validate-rail.png')

    // ── 5b. 소스 보기 — 레일이 파일 트리 + 읽기 전용 뷰어로 전환 (M7) ──
    await page.getByTestId('builder-open-source').click()
    await expect(rail.getByTestId('builder-source-pane')).toBeVisible({ timeout: 15_000 })
    await expect(rail.getByTestId('builder-source-viewer')).toContainText('e2e-notes', {
      timeout: 30_000,
    })
    await settle(page)
    await capture(page, WAVE, '05b-source-pane.png')
    await page.getByTestId('builder-open-source').click()
    await expect(rail.getByTestId('builder-status-rows')).toBeVisible({ timeout: 15_000 })

    // ── 6. 시험 승인 카드 + "이 세션에서 계속 허용" ────────────────────
    await sendMessage('E2E_SKILL_BUILDER_TEST run=1')
    await expect(page.getByText('승인이 필요합니다').last()).toBeVisible({ timeout: 45_000 })
    const consent = page.getByTestId('approval-session-consent').last()
    await expect(consent).toBeVisible()
    await consent.check()
    await settle(page, 300)
    await capture(page, WAVE, '06-test-approval-consent.png')

    // ── 7. 승인 → 샌드박스 실행 완료 ───────────────────────────────────
    await approveWithRetry()
    await expect(page.getByText('드래프트 시험 실행이 끝났습니다').last()).toBeVisible({
      timeout: 60_000,
    })
    await settle(page)
    await capture(page, WAVE, '07-test-executed.png')

    // ── 8. 동의 후 재실행 — 무카드 ─────────────────────────────────────
    await sendMessage('E2E_SKILL_BUILDER_RETEST run=2')
    await expect(page.getByText('드래프트 시험 실행이 끝났습니다').nth(1)).toBeVisible({
      timeout: 60_000,
    })
    await expect(page.getByText('승인이 필요합니다')).toHaveCount(0)
    await settle(page)
    await capture(page, WAVE, '08-retest-no-card.png')

    // ── 9. finalize — 항상 승인 카드 (동의 옵션 없음) ──────────────────
    await sendMessage('E2E_SKILL_BUILDER_FINALIZE')
    await expect(page.getByText('finalize_skill').last()).toBeVisible({ timeout: 45_000 })
    await expect(page.getByTestId('approval-session-consent')).toHaveCount(0)
    await settle(page, 300)
    await capture(page, WAVE, '09-finalize-card.png')

    // ── 10. 승인 → 완료 배너 + 딥링크 ─────────────────────────────────
    await approveWithRetry()
    await expect(page.getByText('스킬을 저장했습니다').last()).toBeVisible({ timeout: 60_000 })
    await expect(page.getByTestId('builder-completed-banner')).toBeVisible({ timeout: 30_000 })
    await settle(page)
    await capture(page, WAVE, '10-finalized-completed.png')

    // ── 11. 리로드 replay — 레일 복원 ──────────────────────────────────
    await page.reload({ waitUntil: 'domcontentloaded' })
    await expect(page.getByTestId('skill-builder-rail')).toBeVisible({ timeout: 60_000 })
    await expect(page.getByTestId('builder-draft-files')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByTestId('builder-completed-banner')).toBeVisible({ timeout: 30_000 })
    await settle(page)
    await capture(page, WAVE, '11-reload-replay.png')

    // ── 12. 딥링크 → 생성된 스킬 상세 다이얼로그 ───────────────────────
    await page.getByRole('link', { name: '스킬 열기' }).click()
    await page.waitForURL(/\/skills\?detailId=/, { timeout: 60_000 })
    await expect(page.getByRole('button', { name: '대화로 개선' })).toBeVisible({
      timeout: 30_000,
    })
    await settle(page)
    await capture(page, WAVE, '12-created-skill-detail.png')

    // ── 13. 대화로 개선 → improve 세션 (원본 시드) ─────────────────────
    await page.getByRole('button', { name: '대화로 개선' }).click()
    await page.waitForURL(/\/skills\/builder\/[0-9a-f-]{36}/, { timeout: 120_000 })
    await expect(page.getByTestId('skill-builder-rail')).toBeVisible({ timeout: 60_000 })
    await expect(page.getByText('개선', { exact: true }).first()).toBeVisible({ timeout: 15_000 })
    await settle(page)
    await capture(page, WAVE, '13-improve-entry.png')

    // ── 14. improve 시드 확인 — 검증 런의 brief가 원본 파일을 보여준다 ──
    await sendMessage('E2E_SKILL_BUILDER_VALIDATE')
    await expect(page.getByText('드래프트 검증을 실행했습니다').last()).toBeVisible({
      timeout: 45_000,
    })
    await expect(
      page.getByTestId('skill-builder-rail').getByTestId('builder-draft-files'),
    ).toBeVisible({ timeout: 30_000 })
    await settle(page)
    await capture(page, WAVE, '14-improve-seeded-rail.png')

    // ── 15. 세션 unavailable 상태 ──────────────────────────────────────
    await page.goto('/skills/builder/00000000-0000-4000-8000-000000000000', {
      waitUntil: 'domcontentloaded',
    })
    await expect(page.getByText('빌더 세션을 열 수 없습니다')).toBeVisible({ timeout: 30_000 })
    await settle(page)
    await capture(page, WAVE, '15-session-unavailable.png')
  })
})
