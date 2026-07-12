import fs from 'node:fs/promises'
import path from 'node:path'
import { test, expect, loginApi, API_BASE } from '../fixtures'
import {
  capture,
  DESKTOP_VIEWPORT,
  settle,
  TINY_PNG_BASE64,
  warmUpChatRoute,
} from './_capture-helpers'

/**
 * 스킬 빌더 Phase 1.5 캡처 투어 — 신규 기능 2건의 실제 동작 증빙.
 *
 * A. 자동 첫 메시지: 다이얼로그의 user_request가 빌더 진입 시 타이핑 없이
 *    자동 발화 + 리로드 시 재전송 없음 (create/improve 공통).
 * B. 바이너리 finalize: 워크스페이스에 실제 PNG asset을 둔 드래프트가
 *    finalize에 성공하고(구버전은 BINARY_FILES_UNSUPPORTED fail-closed),
 *    저장된 스킬 디스크에 바이트가 그대로 보존된다.
 *
 * 전제: throwaway 스택 + scripted system LLM (E2E_LLM_* 비움).
 * E2E_CAPTURE_TOUR=1 게이트 (일반 실행에선 skip).
 */

const WAVE = 'skill-builder-phase15'
// playwright cwd = frontend/ — 백엔드 data_root(./data)는 형제 디렉토리.
const BACKEND_DATA = path.join('..', 'backend', 'data')

const CREATE_REQUEST = '회의록에서 담당자, 할 일, 마감일을 표로 정리하는 스킬을 만들어줘'
const IMPROVE_REQUEST = '요약 형식을 표 기반으로 개선해줘'
const SCRIPTED_REPLY = 'E2E scripted document model is ready.'

test.describe('스킬 빌더 Phase 1.5 — 자동 첫 메시지 + 바이너리 finalize 캡처', () => {
  test.skip(process.env.E2E_CAPTURE_TOUR !== '1', 'Set E2E_CAPTURE_TOUR=1 to run the capture tour')
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  test.beforeAll(async ({ browser }) => {
    test.setTimeout(300_000)
    await warmUpChatRoute(browser)
  })

  test('A. create 다이얼로그 요청 → 진입 즉시 자동 발화 → 리로드 중복 없음', async ({
    page,
    request,
  }) => {
    test.setTimeout(300_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)
    await loginApi(request)

    // ── 1. 요청의 출처: "대화로 만들기" 다이얼로그 ─────────────────────
    await page.goto('/skills', { waitUntil: 'domcontentloaded', timeout: 120_000 })
    await page.getByRole('button', { name: '대화로 만들기' }).first().click()
    const requestBox = page.locator('#skill-chat-request')
    await expect(requestBox).toBeVisible({ timeout: 15_000 })
    await requestBox.fill(CREATE_REQUEST)
    await settle(page, 300)
    await capture(page, WAVE, '01-create-dialog-request.png')

    // ── 2. 대화 시작 → 빌더 진입 — 타이핑 없이 첫 턴이 자동 완료된다 ──
    await page.getByRole('button', { name: '대화 시작' }).click()
    await page.waitForURL(/\/skills\/builder\/[0-9a-f-]{36}/, { timeout: 120_000 })
    const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
    await expect(composer).toBeVisible({ timeout: 60_000 })
    // emptyContent 힌트 패널의 user_request echo와 구분되게 메시지 버블로 스코프.
    const autoSentBubble = page
      .locator('[data-moldy-message-id]')
      .filter({ hasText: CREATE_REQUEST })
    await expect(autoSentBubble.first()).toBeVisible({ timeout: 45_000 })
    await expect(page.getByText(SCRIPTED_REPLY).last()).toBeVisible({ timeout: 45_000 })
    await expect(composer).toHaveValue('') // 사용자는 아무것도 입력하지 않았다
    await settle(page)
    await capture(page, WAVE, '02-auto-first-message.png')

    // ── 3. 리로드 — 서버 진실 가드(메시지/run 이력)로 재전송이 없다 ────
    await page.reload({ waitUntil: 'domcontentloaded' })
    await expect(page.getByText(SCRIPTED_REPLY).first()).toBeVisible({ timeout: 60_000 })
    await settle(page, 1_500) // 재발화가 있었다면 이 사이 낙관 버블이 생긴다
    await expect(autoSentBubble).toHaveCount(1)
    await capture(page, WAVE, '03-reload-no-duplicate.png')
  })

  test('B. improve 세션도 진입 즉시 자동 발화', async ({ page, request }) => {
    test.setTimeout(300_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)
    const csrf = await loginApi(request)

    const skillRes = await request.post(`${API_BASE}/api/skills`, {
      headers: csrf,
      data: {
        name: '회의 요약 도우미',
        content:
          '---\nname: meeting-summary\ndescription: "Use when summarizing meetings."\n---\n\n회의 내용을 요약한다.\n',
      },
    })
    expect(skillRes.ok(), `create skill → ${skillRes.status()}`).toBeTruthy()
    const skill = (await skillRes.json()) as { id: string }

    const sessionRes = await request.post(`${API_BASE}/api/skill-builder`, {
      headers: csrf,
      data: { mode: 'improve', user_request: IMPROVE_REQUEST, source_skill_id: skill.id },
    })
    expect(sessionRes.ok(), `improve start → ${sessionRes.status()}`).toBeTruthy()
    const session = (await sessionRes.json()) as { id: string }

    await page.goto(`/skills/builder/${session.id}`, {
      waitUntil: 'domcontentloaded',
      timeout: 120_000,
    })
    await expect(
      page.locator('[data-moldy-message-id]').filter({ hasText: IMPROVE_REQUEST }).first(),
    ).toBeVisible({ timeout: 45_000 })
    await expect(page.getByText(SCRIPTED_REPLY).last()).toBeVisible({ timeout: 45_000 })
    await settle(page)
    await capture(page, WAVE, '04-improve-auto-first-message.png')
  })

  test('C. 바이너리 asset(PNG) 포함 드래프트의 finalize 성공', async ({ page, request }) => {
    test.setTimeout(300_000)
    await page.setViewportSize(DESKTOP_VIEWPORT)
    const csrf = await loginApi(request)

    const res = await request.post(`${API_BASE}/api/skill-builder`, {
      headers: csrf,
      data: { mode: 'create', user_request: '로고 이미지 asset을 쓰는 회의록 스킬을 만들어줘' },
    })
    expect(res.ok(), `start v2 → ${res.status()}`).toBeTruthy()
    const session = (await res.json()) as { id: string }
    const sessionId = session.id

    await page.goto(`/skills/builder/${sessionId}`, {
      waitUntil: 'domcontentloaded',
      timeout: 120_000,
    })
    const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
    await expect(composer).toBeVisible({ timeout: 60_000 })
    // 자동 첫 턴 완료 대기 후 마커 진행.
    await expect(page.getByText(SCRIPTED_REPLY).last()).toBeVisible({ timeout: 45_000 })

    const sendMessage = async (text: string) => {
      await composer.fill(text)
      await composer.press('Enter')
      await expect(composer).toHaveValue('', { timeout: 10_000 })
    }

    // ── 1. 드래프트 작성 (scripted write_file 2건) ──────────────────────
    await sendMessage(`E2E_SKILL_BUILDER_WRITE /skill-drafts/${sessionId}`)
    await expect(page.getByText('드래프트 파일을 작성했습니다').last()).toBeVisible({
      timeout: 45_000,
    })

    // ── 2. 바이너리 asset 주입 — 실제 320×200 PNG를 워크스페이스 디스크에 ──
    const pngBytes = Buffer.from(TINY_PNG_BASE64, 'base64')
    const assetDir = path.join(BACKEND_DATA, 'skill-drafts', sessionId, 'assets')
    await fs.mkdir(assetDir, { recursive: true })
    await fs.writeFile(path.join(assetDir, 'logo.png'), pngBytes)

    // ── 3. 검증 — 레일 상태 카드/파일 목록 채움 ────────────────────────
    await sendMessage('E2E_SKILL_BUILDER_VALIDATE')
    await expect(page.getByText('드래프트 검증을 실행했습니다').last()).toBeVisible({
      timeout: 45_000,
    })
    await expect(page.getByTestId('skill-builder-rail').getByTestId('builder-draft-files')).toBeVisible(
      { timeout: 30_000 },
    )
    await settle(page)
    await capture(page, WAVE, '05-binary-draft-validated.png')

    // ── 4. finalize 승인 카드 (구버전이라면 승인 후 BINARY_FILES_UNSUPPORTED) ──
    await sendMessage('E2E_SKILL_BUILDER_FINALIZE')
    await expect(page.getByText('finalize_skill').last()).toBeVisible({ timeout: 45_000 })
    await settle(page, 300)
    await capture(page, WAVE, '06-binary-finalize-approval.png')

    // ── 5. 승인 → 저장 성공 + 완료 배너 ────────────────────────────────
    await page.getByTestId('approval-approve-button').last().click()
    await expect(page.getByText('스킬을 저장했습니다').last()).toBeVisible({ timeout: 60_000 })
    await expect(page.getByTestId('builder-completed-banner')).toBeVisible({ timeout: 30_000 })
    await settle(page)
    await capture(page, WAVE, '07-binary-finalize-completed.png')

    // ── 6. 저장 결과 검증 — 세션 completed + 저장 트리에 바이트 동일 보존 ──
    const after = (await (
      await request.get(`${API_BASE}/api/skill-builder/${sessionId}`)
    ).json()) as { status: string; finalized_skill_id: string | null }
    expect(after.status).toBe('completed')
    expect(after.finalized_skill_id).toBeTruthy()
    const stored = path.join(
      BACKEND_DATA,
      'skills',
      after.finalized_skill_id as string,
      'assets',
      'logo.png',
    )
    const storedBytes = await fs.readFile(stored)
    expect(storedBytes.equals(pngBytes)).toBe(true)

    // ── 7. 완료 딥링크 → 생성된 package 스킬 소스 탭 ───────────────────
    await page.getByRole('link', { name: '스킬 열기' }).click()
    await page.waitForURL(/\/skills\/[^/]+\/source/, { timeout: 60_000 })
    await expect(page.getByRole('button', { name: '대화로 개선' })).toBeVisible({
      timeout: 30_000,
    })
    await settle(page)
    await capture(page, WAVE, '08-binary-skill-detail.png')
  })
})
