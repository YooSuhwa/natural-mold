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

    // M8-1 회귀 가드: 런 직후에도 Enter 전송이 즉시 동작해야 한다
    // (post-run 하이드레이션이 컴포저를 잠그던 결함의 근본 수정 검증 —
    // 폴백 없이 Enter만으로 값이 비워져야 한다).
    const sendMessage = async (text: string) => {
      await composer.fill(text)
      await composer.press('Enter')
      await expect(composer).toHaveValue('', { timeout: 10_000 })
    }

    // M8-2 회귀 가드: 백엔드가 인터럽트 전이를 trace보다 먼저 커밋하고 resume
    // 핸들러가 전이를 짧게 기다리므로, 승인은 재시도 없이 한 번에 수락되어야
    // 한다 (재시도 문구가 뜨면 레이스 회귀).
    const approve = async () => {
      await page.getByTestId('approval-approve-button').last().click()
      await expect(
        page.getByText('승인 응답을 전송하지 못했습니다. 다시 시도하세요.'),
      ).toHaveCount(0)
    }

    // 1) 점진 편집 — write_file 2건이 승인 카드 없이 실행된다 (AD-3 과승인 방지).
    await sendMessage(`E2E_SKILL_BUILDER_WRITE /skill-drafts/${sessionId}`)
    await expect(page.getByText('드래프트 파일을 작성했습니다').last()).toBeVisible({
      timeout: 45_000,
    })

    // 2) 검증 — 다음 런 stream head의 moldy.skill_draft가 레일 파일 목록을 채운다.
    await sendMessage('E2E_SKILL_BUILDER_VALIDATE')
    await expect(page.getByText('드래프트 검증을 실행했습니다').last()).toBeVisible({
      timeout: 45_000,
    })
    const rail = page.getByTestId('skill-builder-rail')
    await expect(rail.getByTestId('builder-draft-files')).toBeVisible({ timeout: 30_000 })
    await expect(rail.getByText('SKILL.md').first()).toBeVisible()
    // M7 상태 카드 — 검증 이벤트가 행 톤 + 런타임 호환 칩을 채운다.
    await expect(rail.getByTestId('builder-status-rows')).toBeVisible({ timeout: 15_000 })
    await expect(rail.getByTestId('builder-runtime-chips')).toBeVisible({ timeout: 15_000 })

    // 2b) 소스 보기 — 파일 API 기반 읽기 전용 뷰어 (M7).
    await page.getByTestId('builder-open-source').click()
    await expect(rail.getByTestId('builder-source-pane')).toBeVisible({ timeout: 15_000 })
    await expect(rail.getByTestId('builder-source-viewer')).toContainText('e2e-notes', {
      timeout: 30_000,
    })
    await page.getByTestId('builder-open-source').click()
    await expect(rail.getByTestId('builder-status-rows')).toBeVisible({ timeout: 15_000 })

    // 3) 드래프트 시험 — CODE_EXECUTION 승인 카드 + 세션 동의 체크.
    await sendMessage('E2E_SKILL_BUILDER_TEST run=1')
    await expect(page.getByText('승인이 필요합니다').last()).toBeVisible({ timeout: 45_000 })
    const consent = page.getByTestId('approval-session-consent').last()
    await expect(consent).toBeVisible()
    await consent.check()
    await approve()
    await expect(page.getByText('드래프트 시험 실행이 끝났습니다').last()).toBeVisible({
      timeout: 60_000,
    })

    // 4) 동의 후 재실행 — 승인 카드 없이 바로 실행된다 (2회차 무카드).
    await sendMessage('E2E_SKILL_BUILDER_RETEST run=2')
    await expect(page.getByText('드래프트 시험 실행이 끝났습니다').nth(1)).toBeVisible({
      timeout: 60_000,
    })
    await expect(page.getByText('승인이 필요합니다')).toHaveCount(0)

    // 5) finalize — 항상 승인 카드, 세션 동의 옵션은 없다.
    await sendMessage('E2E_SKILL_BUILDER_FINALIZE')
    await expect(page.getByText('finalize_skill').last()).toBeVisible({ timeout: 45_000 })
    await expect(page.getByTestId('approval-session-consent')).toHaveCount(0)
    // M8-3 회귀 가드: 직전 resolved 카드(test_skill_draft)와 인터럽트가 다르므로
    // 그룹 컨테이너("승인 대기 N건")로 묶이지 않고 단독 카드로 렌더되어야 한다.
    await expect(page.getByText(/승인 대기 \d+건/)).toHaveCount(0)
    await approve()
    await expect(page.getByText('스킬을 저장했습니다').last()).toBeVisible({ timeout: 60_000 })
    await expect(page.getByTestId('builder-completed-banner')).toBeVisible({ timeout: 30_000 })

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
    // R4: 멀티턴 대화 이력이 checkpointer에서 복원된다 (§2-5 — 레일만이 아니라
    // 트랜스크립트 버블도). 사용자 마커 메시지와 최종 응답 텍스트로 단언.
    await expect(page.getByText('E2E_SKILL_BUILDER_VALIDATE').first()).toBeVisible({
      timeout: 30_000,
    })
    await expect(page.getByText('스킬을 저장했습니다').first()).toBeVisible({ timeout: 30_000 })

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

  test('finalize conflict — source skill changed mid-session, agent explains (§2-3)', async ({
    page,
    request,
  }) => {
    test.setTimeout(180_000)
    // CSRF 토큰은 요청 컨텍스트(쿠키)와 짝이라, 이 테스트의 request 컨텍스트로
    // 다시 로그인해 새 토큰을 받는다 (beforeAll 토큰은 그 컨텍스트 전용).
    const csrfLocal = await login(request)
    // 1) 원본 텍스트 스킬 생성 → improve 세션 시작 → 원본을 밖에서 수정해
    //    content_hash를 어긋나게 만든다 (SOURCE_SKILL_CHANGED 재현).
    const skillRes = await request.post(`${API}/api/skills`, {
      headers: csrfLocal,
      data: {
        name: `e2e-conflict-${Date.now()}`,
        content: '---\nname: e2e-conflict\ndescription: "Use when testing conflicts."\n---\n\noriginal\n',
      },
    })
    expect(skillRes.ok(), `create skill → ${skillRes.status()}`).toBeTruthy()
    const skill = (await skillRes.json()) as { id: string; version: string | null }

    const sessionRes = await request.post(`${API}/api/skill-builder`, {
      headers: csrfLocal,
      data: { mode: 'improve', user_request: '이 스킬 개선해줘', source_skill_id: skill.id },
    })
    expect(sessionRes.ok(), `improve start → ${sessionRes.status()}`).toBeTruthy()
    const improveSession = (await sessionRes.json()) as { id: string }

    const putRes = await request.put(`${API}/api/skills/${skill.id}/content`, {
      headers: csrfLocal,
      data: {
        content: '---\nname: e2e-conflict\ndescription: "Use when testing conflicts."\n---\n\nchanged outside the session\n',
      },
    })
    expect(putRes.ok(), `mutate source → ${putRes.status()}`).toBeTruthy()
    const mutatedHash = ((await putRes.json()) as { content_hash: string }).content_hash

    // 2) finalize 시도 → 승인 카드 → 승인 → 도구가 SOURCE_SKILL_CHANGED 반환 →
    //    에이전트가 사용자에게 설명한다 (§2-3 계약의 결정론 재현).
    await page.goto(`/skills/builder/${improveSession.id}`, {
      waitUntil: 'domcontentloaded',
      timeout: 120_000,
    })
    const composer = page.locator('textarea[data-moldy-composer-input="true"]').last()
    await expect(composer).toBeVisible({ timeout: 60_000 })
    await composer.fill('E2E_SKILL_BUILDER_FINALIZE_CONFLICT')
    await composer.press('Enter')
    await expect(composer).toHaveValue('', { timeout: 10_000 })
    await expect(page.getByText('finalize_skill').last()).toBeVisible({ timeout: 45_000 })
    await page.getByTestId('approval-approve-button').last().click()
    await expect(page.getByText('원본 스킬이 세션 시작 후 변경되어').last()).toBeVisible({
      timeout: 60_000,
    })

    // 3) 세션은 완료되지 않고, 원본 스킬도 세션이 덮어쓰지 않았다.
    const after = (await (
      await request.get(`${API}/api/skill-builder/${improveSession.id}`)
    ).json()) as { status: string; finalized_skill_id: string | null }
    expect(after.status).not.toBe('completed')
    expect(after.finalized_skill_id).toBeNull()
    // 세션이 원본을 덮어쓰지 않았다 — 밖에서 수정한 hash가 그대로여야 한다.
    const skillAfter = (await (await request.get(`${API}/api/skills/${skill.id}`)).json()) as {
      content_hash: string
    }
    expect(skillAfter.content_hash).toBe(mutatedHash)
  })
})
