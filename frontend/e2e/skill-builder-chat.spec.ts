import { test, expect } from './fixtures'
import type { APIRequestContext } from '@playwright/test'

// 스킬 빌더 챗 (skill-studio phase 1, M6) — 스펙 §2 성공 기준 E2E.
// scripted 시퀀스: write_file(드래프트 2파일) → validate_skill →
// test_skill_draft(승인 카드 → "이 세션에서 계속 허용" → 재실행 무카드) →
// finalize_skill(항상 승인 카드 → 승인 → skills row) + 리로드 replay.
// 백엔드 E2E_SCRIPTED_MODEL_ENABLED=true가 system LLM(text_primary)을
// scripted 모델로 시드한다 (seed_e2e_scripted_model).
const API =
  process.env.E2E_API_BASE_URL ?? `http://localhost:${process.env.E2E_BACKEND_PORT ?? '8001'}`
const EMAIL = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const PASSWORD =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'

async function login(request: APIRequestContext): Promise<Record<string, string>> {
  const res = await request.post(`${API}/api/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  })
  expect(res.ok()).toBeTruthy()
  return { 'X-CSRF-Token': (await res.json()).csrf_token as string }
}

test.describe('skill builder chat', () => {
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  let csrf: Record<string, string>
  let sessionId: string
  let conversationId: string

  test.beforeAll(async ({ request }) => {
    csrf = await login(request)
    const res = await request.post(`${API}/api/skill-builder`, {
      headers: csrf,
      data: { mode: 'create', user_request: 'E2E 회의록 스킬을 만들어줘' },
    })
    expect(res.ok(), `start v2 → ${res.status()}`).toBeTruthy()
    const session = (await res.json()) as {
      id: string
      conversation_id: string | null
      agent_id: string | null
    }
    expect(session.conversation_id).toBeTruthy()
    expect(session.agent_id).toBeTruthy()
    sessionId = session.id
    conversationId = session.conversation_id as string
  })

  test('multiturn draft edit → validate → consent-gated test → finalize → reload replay', async ({
    page,
    request,
  }) => {
    test.setTimeout(240_000)
    // 첫 진입은 dev 서버 cold-compile을 견디도록 완화된 대기 조건 사용.
    await page.goto(`/skills/builder/${sessionId}`, {
      waitUntil: 'domcontentloaded',
      timeout: 120_000,
    })

    await expect(page.getByTestId('skill-builder-rail')).toBeVisible({ timeout: 60_000 })
    const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
    await expect(composer).toBeVisible({ timeout: 60_000 })

    // Enter 전송은 런 직후 잠깐 무시될 수 있다(컴포저 상태 전이) — 전송 실패 시
    // 전송 버튼 클릭으로 폴백하고, 비워졌는지로 실제 전송을 확인한다.
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

    // 캡처 투어 (스펙 §2-6) — 각 성공 기준의 시각 증빙. PNG는 gitignore.
    let captureIndex = 0
    const capture = async (name: string) => {
      captureIndex += 1
      await page.screenshot({
        path: `output/captures/skill-builder-chat/${String(captureIndex).padStart(2, '0')}-${name}.png`,
        fullPage: false,
      })
    }

    // 승인 resume은 일시적 전송 실패 시 카드가 재시도 문구를 띄운다 —
    // hitl-approval.spec의 재시도 계약과 동일하게 한 번 더 누른다.
    const approveWithRetry = async () => {
      await page.getByTestId('approval-approve-button').last().click()
      const retryPrompt = page
        .getByText('승인 응답을 전송하지 못했습니다. 다시 시도하세요.')
        .last()
      try {
        await expect(retryPrompt).toBeVisible({ timeout: 5_000 })
        await page.getByTestId('approval-approve-button').last().click()
      } catch {
        // 재시도 문구가 안 떴으면 첫 승인 전송이 수락된 것.
      }
    }

    await capture('entry-empty-builder')

    // 1) 점진 편집 — write_file 2건이 승인 카드 없이 실행된다 (AD-3 과승인 방지).
    await sendMessage(`E2E_SKILL_BUILDER_WRITE /skill-drafts/${sessionId}`)
    await expect(page.getByText('드래프트 파일을 작성했습니다').last()).toBeVisible({
      timeout: 45_000,
    })

    await capture('draft-written')

    // 2) 검증 — 다음 런 stream head의 moldy.skill_draft가 레일 파일 목록을 채운다.
    await sendMessage('E2E_SKILL_BUILDER_VALIDATE')
    await expect(page.getByText('드래프트 검증을 실행했습니다').last()).toBeVisible({
      timeout: 45_000,
    })
    const rail = page.getByTestId('skill-builder-rail')
    await expect(rail.getByTestId('builder-draft-files')).toBeVisible({ timeout: 30_000 })
    await expect(rail.getByText('SKILL.md').first()).toBeVisible()

    await capture('validate-rail')

    // 3) 드래프트 시험 — CODE_EXECUTION 승인 카드 + 세션 동의 체크.
    await sendMessage('E2E_SKILL_BUILDER_TEST run=1')
    await expect(page.getByText('승인이 필요합니다').last()).toBeVisible({ timeout: 45_000 })
    const consent = page.getByTestId('approval-session-consent').last()
    await expect(consent).toBeVisible()
    await consent.check()
    await capture('test-approval-card-consent')
    await approveWithRetry()
    await expect(page.getByText('드래프트 시험 실행이 끝났습니다').last()).toBeVisible({
      timeout: 60_000,
    })

    // 4) 동의 후 재실행 — 승인 카드 없이 바로 실행된다 (2회차 무카드).
    await sendMessage('E2E_SKILL_BUILDER_RETEST run=2')
    await expect(page.getByText('드래프트 시험 실행이 끝났습니다').nth(1)).toBeVisible({
      timeout: 60_000,
    })
    await expect(page.getByText('승인이 필요합니다')).toHaveCount(0)

    await capture('retest-no-card')

    // 5) finalize — 항상 승인 카드, 세션 동의 옵션은 없다. 직전 resolved 카드와
    // 연속 request_approval로 묶여 그룹 컨테이너("승인 대기 N건")로 렌더될 수
    // 있으므로 헤더 대신 finalize_skill 카드 자체를 기다린다.
    await sendMessage('E2E_SKILL_BUILDER_FINALIZE')
    await expect(page.getByText('finalize_skill').last()).toBeVisible({ timeout: 45_000 })
    await expect(page.getByTestId('approval-session-consent')).toHaveCount(0)
    await approveWithRetry()
    await expect(page.getByText('스킬을 저장했습니다').last()).toBeVisible({ timeout: 60_000 })
    await expect(page.getByTestId('builder-completed-banner')).toBeVisible({ timeout: 30_000 })
    await capture('finalized-completed-rail')

    // 6) 진짜 skills row + 세션 completed (스펙 §2-3).
    const skills = (await (
      await request.get(`${API}/api/skills`)
    ).json()) as { slug: string; kind: string }[]
    expect(skills.some((s) => s.slug.startsWith('e2e-notes') && s.kind === 'package')).toBe(true)
    const session = (await (
      await request.get(`${API}/api/skill-builder/${sessionId}`)
    ).json()) as { status: string; finalized_skill_id: string | null }
    expect(session.status).toBe('completed')
    expect(session.finalized_skill_id).toBeTruthy()

    // 7) 리로드 replay — 레일 복원(파일 목록) + <redacted> 부재 (스펙 §2-4/§7).
    await page.reload({ waitUntil: 'domcontentloaded' })
    await expect(page.getByTestId('skill-builder-rail')).toBeVisible({ timeout: 60_000 })
    await expect(page.getByTestId('builder-draft-files')).toBeVisible({ timeout: 30_000 })
    await expect(page.getByTestId('builder-completed-banner')).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('body')).not.toContainText('<redacted>')
    await capture('reload-replay-restored')

    // 대화가 히든 에이전트 소유라 네비게이터/에이전트 목록에 새지 않는다 (§2 리스크).
    const agents = (await (await request.get(`${API}/api/agents`)).json()) as { id: string }[]
    const conversations = (await (
      await request.get(`${API}/api/conversations/page?limit=50`)
    ).json()) as { items: { id: string }[] }
    expect(conversations.items.map((c) => c.id)).not.toContain(conversationId)
    expect(agents.map((a) => a.id)).not.toContain(
      (await (await request.get(`${API}/api/skill-builder/${sessionId}`)).json()).agent_id,
    )
  })
})
