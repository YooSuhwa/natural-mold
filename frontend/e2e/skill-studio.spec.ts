import { test, expect } from './fixtures'

// Phase 2 — 스킬 스튜디오 6탭 IA: 탭 내비/컨텍스트 바/스킬 스위처/빌더 인덱스.

const now = '2026-07-11T00:00:00.000Z'

function makeSkill(id: string, name: string, kind: 'text' | 'package') {
  return {
    id,
    name,
    slug: id,
    description: `${name} 설명`,
    kind,
    version: '1.0.0',
    storage_path: null,
    content_hash: `hash-${id}`,
    size_bytes: 256,
    used_by_count: 1,
    package_metadata: null,
    current_revision_id: null,
    credential_requirements: null,
    execution_profile: null,
    health: null,
    latest_evaluation_summary: null,
    last_modified_at: now,
    created_at: now,
    updated_at: now,
    origin_summary: null,
    publication_summary: null,
    installation: null,
  }
}

const alpha = makeSkill('skill-alpha', 'Alpha Notes', 'text')
const beta = makeSkill('skill-beta', 'Beta Package', 'package')
const skills = [alpha, beta]

const builderBrief = {
  id: '00000000-0000-4000-8000-00000000b1de',
  mode: 'improve',
  status: 'active',
  user_request: 'Alpha Notes를 개선해줘',
  source_skill_id: alpha.id,
  finalized_skill_id: null,
  conversation_id: null,
  created_at: now,
  updated_at: now,
}

async function mockStudioApis(page: import('@playwright/test').Page) {
  const sessionListRequests: Array<string | null> = []
  await page.route('**/api/skill-builder**', (route) => {
    const url = new URL(route.request().url())
    if (route.request().method() === 'GET' && url.pathname === '/api/skill-builder') {
      const scoped = url.searchParams.get('skill_id')
      sessionListRequests.push(scoped)
      // 무스코프 요청에는 다른 픽스처 — 스코핑 회귀가 세션 이력 단언으로
      // 위장 통과하는 토톨로지를 차단한다.
      return route.fulfill({
        json: scoped === alpha.id ? [builderBrief] : [],
      })
    }
    return route.fulfill({ status: 404, json: { detail: url.pathname } })
  })
  await page.route('**/api/skills**', (route) => {
    const url = new URL(route.request().url())
    const method = route.request().method()
    const pathName = url.pathname

    if (method !== 'GET') {
      return route.fulfill({ status: 405, json: { detail: 'read-only fixture' } })
    }
    if (pathName === '/api/skills') {
      return route.fulfill({ json: skills })
    }
    const detail = skills.find((skill) => pathName === `/api/skills/${skill.id}`)
    if (detail) {
      return route.fulfill({ json: detail })
    }
    if (pathName === `/api/skills/${alpha.id}/content`) {
      return route.fulfill({ json: { content: '# Alpha Notes\n요약 규칙 본문' } })
    }
    if (/\/api\/skills\/skill-(alpha|beta)\/revisions$/.test(pathName)) {
      return route.fulfill({ json: [] })
    }
    if (/\/api\/skills\/skill-(alpha|beta)\/evaluations$/.test(pathName)) {
      return route.fulfill({ json: [] })
    }
    if (/\/api\/skills\/skill-(alpha|beta)\/credential-(requirements|bindings)$/.test(pathName)) {
      return route.fulfill({ json: [] })
    }
    return route.fulfill({ status: 404, json: { detail: pathName } })
  })
  return { sessionListRequests }
}

test.describe('Skill studio IA', () => {
  test('list rows navigate to skill tabs; scoped tabs disabled without context', async ({
    page,
  }) => {
    await mockStudioApis(page)

    await page.goto('/skills')
    await expect(page.getByText('Alpha Notes')).toBeVisible()
    // 목록 탭에서는 스킬 스코프 탭이 비활성이다.
    await expect(page.getByTestId('studio-tab-source')).toBeDisabled()
    await expect(page.getByTestId('studio-context-bar')).toBeHidden()

    // 행 클릭 → 소스 탭.
    await page.getByText('Alpha Notes').click()
    await page.waitForURL(/\/skills\/skill-alpha\/source/)
    await expect(page.getByTestId('studio-context-bar')).toContainText('Alpha Notes')
    await expect(page.getByTestId('studio-context-bar')).toContainText('연결 에이전트')

    // 탭 내비게이션: 평가 → 버전 → 설정.
    await page.getByTestId('studio-tab-evaluation').click()
    await page.waitForURL(/\/skills\/skill-alpha\/evaluation/)
    await page.getByTestId('studio-tab-versions').click()
    await page.waitForURL(/\/skills\/skill-alpha\/versions/)
    await page.getByTestId('studio-tab-settings').click()
    await page.waitForURL(/\/skills\/skill-alpha\/settings/)
    await expect(page.getByText('메타데이터')).toBeVisible()
    await expect(page.getByRole('button', { name: '스킬 삭제' })).toBeVisible()
  })

  test('skill switcher keeps the active tab', async ({ page }) => {
    await mockStudioApis(page)

    await page.goto('/skills/skill-alpha/versions')
    await expect(page.getByTestId('studio-context-bar')).toContainText('Alpha Notes')

    await page.getByTestId('studio-skill-switcher').click()
    await page.getByRole('menuitem', { name: /Beta Package/ }).click()

    await page.waitForURL(/\/skills\/skill-beta\/versions/)
    await expect(page.getByTestId('studio-context-bar')).toContainText('Beta Package')
  })

  test('builder tab lands on the scoped builder index with session history', async ({ page }) => {
    const { sessionListRequests } = await mockStudioApis(page)

    await page.goto('/skills/skill-alpha/source')
    await page.getByTestId('studio-tab-builder').click()

    await page.waitForURL(/\/skills\/builder\?skillId=skill-alpha/)
    // 셸이 ?skillId= 스코프를 인식해 컨텍스트 유지 — 스킬 탭이 disabled로
    // 오표기되거나 "새 스킬 초안"으로 바뀌면 안 된다 (리뷰 R 회귀 가드).
    await expect(page.getByTestId('studio-context-bar')).toContainText('Alpha Notes')
    await expect(page.getByTestId('studio-tab-source')).toBeEnabled()
    await expect(page.getByRole('button', { name: /Alpha Notes 개선 시작/ })).toBeVisible()
    const sessionList = page.getByTestId('builder-session-list')
    await expect(sessionList).toContainText('Alpha Notes를 개선해줘')
    await expect(sessionList.getByRole('link').first()).toHaveAttribute(
      'href',
      `/skills/builder/${builderBrief.id}`,
    )
    // 목록 요청 자체가 skill_id로 스코프됐는지 — mock 픽스처 분기와 이중 방어.
    expect(sessionListRequests).toContain(alpha.id)
  })
})
