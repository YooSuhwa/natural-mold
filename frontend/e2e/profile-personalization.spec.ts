import { z } from 'zod'

import { API_BASE, apiGetJson, apiJson, test, expect } from './fixtures'
import { registerMember } from './helpers/member-session'
import { captureResourcePage } from './helpers/capture-resource-page'

const profileSchema = z.object({
  display_name: z.string().nullable(),
  avatar_mode: z.string(),
  avatar_initials: z.string().nullable(),
  avatar_color: z.string(),
  avatar_image_url: z.string().nullable(),
})

test.use({ storageState: { cookies: [], origins: [] } })
test.beforeEach(async ({ page }) => {
  await registerMember(page)
})

test('profile name, initials and color survive reload; automatic identity can be restored', async ({
  page,
  errors,
}, testInfo) => {
  await page.goto('/settings')
  await page.getByLabel('표시 이름', { exact: true }).fill('프로필 검증')
  await page.getByRole('button', { name: '문자 사용', exact: true }).click()
  await page.getByLabel('아이콘 문자', { exact: true }).fill('검증')
  await page.getByRole('button', { name: '바이올렛', exact: true }).click()
  const saved = page.waitForResponse(
    (res) => res.url().endsWith('/api/auth/me/profile') && res.request().method() === 'PATCH',
  )
  await page.getByRole('button', { name: '저장', exact: true }).click()
  const profile = profileSchema.parse(await apiJson(await saved, 'Save profile'))
  expect(profile).toMatchObject({
    display_name: '프로필 검증',
    avatar_mode: 'initials',
    avatar_initials: '검증',
    avatar_color: 'violet',
  })
  await page.reload()
  await expect(page.getByLabel('표시 이름', { exact: true })).toHaveValue('프로필 검증')
  await expect(page.getByLabel('아이콘 문자', { exact: true })).toHaveValue('검증')
  await expect(page.getByRole('button', { name: '바이올렛', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  await expect(page.getByRole('img', { name: '프로필 검증 프로필 아이콘' }).first()).toContainText(
    '검증',
  )
  await captureResourcePage({
    page,
    testInfo,
    state: 'profile-personalized',
    evidence: page.getByLabel('표시 이름', { exact: true }),
  })

  await page.getByLabel('표시 이름', { exact: true }).fill('')
  await page.getByRole('button', { name: '자동', exact: true }).click()
  const reset = page.waitForResponse(
    (res) => res.url().endsWith('/api/auth/me/profile') && res.request().method() === 'PATCH',
  )
  await page.getByRole('button', { name: '저장', exact: true }).click()
  expect(profileSchema.parse(await apiJson(await reset, 'Reset profile'))).toMatchObject({
    display_name: null,
    avatar_mode: 'auto',
  })
  await page.reload()
  await expect(page.getByLabel('표시 이름', { exact: true })).toHaveValue('')
  await expect(page.getByRole('button', { name: '자동', exact: true })).toHaveAttribute(
    'aria-pressed',
    'true',
  )
  expect(errors.console).toEqual([])
  expect(errors.network).toEqual([])
})

test('avatar image upload, authenticated rendering and deletion persist', async ({
  page,
  errors,
}) => {
  await page.goto('/settings')
  const uploaded = page.waitForResponse(
    (res) => res.url().endsWith('/api/auth/me/avatar-image') && res.request().method() === 'POST',
  )
  // A real 1x1 PNG: no external image service or production user data.
  await page.locator('input[type="file"]').setInputFiles({
    name: 'avatar.png',
    mimeType: 'image/png',
    buffer: Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aM1sAAAAASUVORK5CYII=',
      'base64',
    ),
  })
  expect(profileSchema.parse(await apiJson(await uploaded, 'Upload avatar'))).toMatchObject({
    avatar_mode: 'image',
    avatar_image_url: expect.any(String),
  })
  await page.reload()
  const image = page
    .locator('img')
    .and(page.getByRole('img', { name: 'E2E Member 프로필 아이콘' }))
    .first()
  await expect(image).toBeVisible()
  await expect
    .poll(() =>
      image.evaluate(
        (element) =>
          element instanceof HTMLImageElement && element.complete && element.naturalWidth > 0,
      ),
    )
    .toBe(true)
  const deleted = page.waitForResponse(
    (res) => res.url().endsWith('/api/auth/me/avatar-image') && res.request().method() === 'DELETE',
  )
  await page.getByRole('button', { name: '이미지 삭제', exact: true }).click()
  expect(profileSchema.parse(await apiJson(await deleted, 'Delete avatar'))).toMatchObject({
    avatar_mode: 'initials',
    avatar_image_url: null,
  })
  await page.reload()
  await expect(page.getByRole('button', { name: '이미지 삭제', exact: true })).toBeDisabled()
  expect(
    profileSchema.parse(await apiGetJson(page.request, `${API_BASE}/api/auth/me`)),
  ).toMatchObject({ avatar_mode: 'initials', avatar_image_url: null })
  expect(errors.console).toEqual([])
  expect(errors.network).toEqual([])
})
