import { test, expect } from './fixtures'

// E2E: Skills page — create text skill.

test.describe('Skills page', () => {
  test('user can create a text skill and see it in the table', async ({ page }) => {
    let skills: Array<Record<string, unknown>> = []
    await page.route(/\/api\/skills(\?.*)?$/, (route) => {
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON() as Record<string, unknown>
        const created = {
          id: 'skill-1',
          name: body.name,
          slug: 'snippet',
          description: body.description ?? null,
          kind: 'text',
          version: null,
          storage_path: null,
          content_hash: 'abc',
          size_bytes: ((body.content as string) ?? '').length,
          used_by_count: 0,
          package_metadata: null,
          last_modified_at: new Date().toISOString(),
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        }
        skills = [created]
        return route.fulfill({ status: 201, json: created })
      }
      return route.fulfill({ json: skills })
    })

    await page.goto('/skills')
    await page
      .getByRole('button', { name: /새 스킬|첫 스킬 만들기/ })
      .first()
      .click()

    await page.getByRole('tab', { name: '텍스트' }).click()
    await page.getByLabel(/이름/).fill('Greeting snippet')
    await page.getByLabel(/내용 \(마크다운\)/).fill('# Hello\nThis is a snippet.')

    await page.getByRole('button', { name: '저장' }).click()
    await expect(page.getByText('스킬이 생성되었습니다')).toBeVisible()
    await expect(page.getByText('Greeting snippet')).toBeVisible()
  })

  test('user can bulk-delete selected skills from the table', async ({ page }) => {
    const now = new Date().toISOString()
    function makeSkill(id: string, name: string, usedByCount: number) {
      return {
        id,
        name,
        slug: id,
        description: null,
        kind: 'text',
        version: null,
        storage_path: null,
        content_hash: 'abc',
        size_bytes: 10,
        used_by_count: usedByCount,
        package_metadata: null,
        health: null,
        latest_evaluation_summary: null,
        last_modified_at: now,
        created_at: now,
        updated_at: now,
      }
    }
    let skills = [makeSkill('skill-a', 'Bulk Target A', 1), makeSkill('skill-b', 'Bulk Target B', 0)]
    const deleted: string[] = []

    await page.route(/\/api\/skills(\?.*)?$/, (route) => route.fulfill({ json: skills }))
    await page.route(/\/api\/skills\/skill-[ab]$/, (route) => {
      const id = new URL(route.request().url()).pathname.split('/').at(-1) ?? ''
      if (route.request().method() === 'DELETE') {
        deleted.push(id)
        skills = skills.filter((skill) => skill.id !== id)
        return route.fulfill({ status: 204, body: '' })
      }
      const match = skills.find((skill) => skill.id === id)
      return match
        ? route.fulfill({ json: match })
        : route.fulfill({ status: 404, json: { detail: 'not found' } })
    })

    await page.goto('/skills')
    await expect(page.getByText('Bulk Target A')).toBeVisible()

    // 행 체크박스로 선택(전체선택 아님) — 체크 클릭이 행 내비게이션으로
    // 새면 안 된다(리뷰 R에서 발견된 실버그의 회귀 가드).
    for (const name of ['Bulk Target A', 'Bulk Target B']) {
      await page
        .getByRole('row')
        .filter({ hasText: name })
        .getByRole('checkbox', { name: '행 선택' })
        .check()
    }
    await expect(page).toHaveURL(/\/skills$/)
    await expect(page.getByTestId('skill-bulk-bar')).toContainText('2개 선택됨')
    await page.getByTestId('skill-bulk-bar').getByRole('button', { name: '삭제' }).click()

    const dialog = page.getByRole('alertdialog')
    await expect(dialog).toContainText('스킬 2개 삭제')
    await expect(dialog).toContainText('Bulk Target A')
    await expect(dialog).toContainText('연결된 에이전트 1개')
    await dialog.getByRole('button', { name: '삭제' }).click()

    await expect.poll(() => deleted.length, { timeout: 15_000 }).toBe(2)
    await expect(page.getByText('스킬 2개를 삭제했습니다')).toBeVisible()
    await expect(page.getByText('Bulk Target A')).toBeHidden()
    // 삭제 후 선택 상태가 리셋된다 (key remount 계약).
    await expect(page.getByTestId('skill-bulk-bar')).toBeHidden()
  })
})
