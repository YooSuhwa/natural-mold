import { mkdir } from 'node:fs/promises'
import path from 'node:path'

import { test, expect, isRecord } from './fixtures'

test.describe('Skill Builder readiness', () => {
  test('shows System LLM setup guidance and keeps direct creation paths available', async ({
    page,
  }) => {
    await page.route('**/api/skills**', (route) => {
      const url = new URL(route.request().url())
      if (route.request().method() === 'GET' && url.pathname === '/api/skills') {
        return route.fulfill({ json: [] })
      }
      return route.fulfill({ status: 404, json: { detail: url.pathname } })
    })

    await page.route('**/api/skill-builder', (route) => {
      const body: unknown = route.request().postDataJSON()
      expect(isRecord(body)).toBeTruthy()
      if (isRecord(body)) {
        expect(body.mode).toBe('create')
        expect(body.user_request).toBe('회의록 액션 아이템 스킬을 만들어줘')
      }
      return route.fulfill({
        status: 409,
        json: {
          error: {
            code: 'SYSTEM_LLM_NOT_CONFIGURED',
            message: 'Skill Builder requires the text_primary system model to be configured.',
          },
          role: 'text_primary',
        },
      })
    })

    await page.goto('/skills')
    await page.getByRole('button', { name: /새 스킬|대화로 첫 스킬 만들기/ }).first().click()
    await page.getByLabel(/요청/).fill('회의록 액션 아이템 스킬을 만들어줘')
    await page.getByRole('button', { name: '대화 시작' }).click()

    const builderDialog = page.getByRole('dialog', { name: '대화로 스킬 만들기' })
    await expect(builderDialog).toBeVisible()
    await builderDialog.getByRole('button', { name: '대화 시작' }).click()

    await expect(builderDialog.getByText('시스템 LLM 설정이 필요합니다')).toBeVisible()
    await expect(
      builderDialog.getByText(
        '스킬 빌더는 text_primary 시스템 모델을 사용합니다. System LLM 설정에서 모델과 시스템 자격증명을 연결하세요. 설정 전에도 텍스트 또는 패키지 업로드는 계속 사용할 수 있습니다.',
      ),
    ).toBeVisible()
    await expect(builderDialog.getByRole('button', { name: 'System LLM 설정 열기' })).toHaveAttribute(
      'href',
      '/settings/system-llm',
    )

    const captureDir = path.resolve(process.cwd(), '../output/e2e-captures/20260615-skill-builder')
    await mkdir(captureDir, { recursive: true })
    await page.screenshot({
      path: path.join(captureDir, 'builder-system-llm-readiness.png'),
      fullPage: false,
    })

    await builderDialog.getByRole('button', { name: '닫기' }).first().click()
    await page.getByRole('button', { name: /새 스킬|대화로 첫 스킬 만들기/ }).first().click()
    const createDialog = page.getByRole('dialog', { name: '새 스킬' })
    await createDialog.getByRole('tab', { name: '텍스트' }).click()
    await expect(createDialog.getByLabel('이름')).toBeVisible()
    await createDialog.getByRole('tab', { name: '패키지' }).click()
    await expect(createDialog.getByRole('button', { name: '업로드' })).toBeVisible()
  })
})
