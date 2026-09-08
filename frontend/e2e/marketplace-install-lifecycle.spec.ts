import { randomUUID } from 'node:crypto'
import type { APIRequestContext } from '@playwright/test'
import { z } from 'zod'

import {
  API_BASE,
  apiDeleteOk,
  apiGetJson,
  apiJson,
  apiPostJson,
  expect,
  loginApi,
  test,
  type CsrfHeaders,
} from './fixtures'
import { registerMember } from './helpers/member-session'

const idSchema = z.object({ id: z.string() })
const installationSchema = z.object({
  id: z.string(),
  version_id: z.string(),
  installed_skill_id: z.string(),
  install_status: z.enum(['active', 'needs_setup', 'disabled', 'uninstalled']),
  is_dirty: z.boolean(),
})
const marketplaceItemSchema = z.object({
  id: z.string(),
  latest_version: z.object({ id: z.string(), version_label: z.string() }),
  installation: z.object({
    installation_id: z.string().nullable(),
    installed_resource_id: z.string().nullable(),
    status: z.enum(['active', 'needs_setup', 'disabled', 'uninstalled']).nullable(),
    update_available: z.boolean(),
    dirty: z.boolean(),
  }),
})

function skillMarkdown(slug: string, version: string, body: string): string {
  return `---\nname: ${slug}\ndescription: "Marketplace lifecycle E2E skill."\nversion: "${version}"\n---\n\n${body}\n`
}

function skillDraft(name: string, slug: string) {
  return {
    name,
    slug,
    description: 'Marketplace credential and update lifecycle fixture.',
    files: [
      {
        path: 'SKILL.md',
        content: skillMarkdown(slug, '1.0.0', 'Initial marketplace lifecycle instructions.'),
        role: 'skill',
      },
    ],
    credential_requirements: [
      {
        key: 'srt_login',
        definition_key: 'srt_account',
        required: true,
        label: 'SRT 계정',
        description: '설치 후 실행에 사용할 테스트 계정입니다.',
        fields: ['username', 'password'],
        injection: 'env',
        scope: 'user',
        env_map: { username: 'SRT_USERNAME', password: 'SRT_PASSWORD' },
      },
    ],
    execution_profile: { support_level: 'one_click', requires_network: false },
  }
}

async function createSourceSkill(
  request: APIRequestContext,
  csrfHeaders: CsrfHeaders,
  name: string,
  slug: string,
): Promise<string> {
  const session = idSchema.parse(
    await apiPostJson(request, `${API_BASE}/api/skill-builder`, csrfHeaders, {
      mode: 'create',
      user_request: `Create isolated marketplace fixture ${slug}`,
    }),
  )
  await apiPostJson(
    request,
    `${API_BASE}/api/skill-builder/${session.id}/validate`,
    csrfHeaders,
    skillDraft(name, slug),
  )
  return idSchema.parse(
    await apiPostJson(request, `${API_BASE}/api/skill-builder/${session.id}/confirm`, csrfHeaders),
  ).id
}

test('configures a needs-setup install and safely overwrites a dirty update', async ({
  page,
  request,
  errors,
}) => {
  test.setTimeout(150_000)
  const publisherCsrf = await loginApi(request)
  const unique = randomUUID()
  const name = `E2E Marketplace Lifecycle ${unique}`
  const slug = `e2e-marketplace-lifecycle-${unique}`
  const credentialName = `E2E SRT ${unique}`
  let sourceSkillId: string | undefined
  let itemId: string | undefined
  let installationId: string | undefined
  let installedSkillId: string | undefined
  let credentialId: string | undefined
  let memberCsrf: CsrfHeaders | undefined

  try {
    sourceSkillId = await createSourceSkill(request, publisherCsrf, name, slug)
    const published = marketplaceItemSchema.parse(
      await apiPostJson(
        request,
        `${API_BASE}/api/marketplace/items/from-skill/${sourceSkillId}`,
        publisherCsrf,
        {
          visibility: 'unlisted',
          name,
          description: 'Credential setup and dirty update E2E fixture.',
        },
      ),
    )
    itemId = published.id

    const member = await registerMember(page)
    memberCsrf = { 'X-CSRF-Token': member.csrf_token }
    const credential = idSchema.parse(
      await apiPostJson(page.request, `${API_BASE}/api/credentials`, memberCsrf, {
        definition_key: 'srt_account',
        name: credentialName,
        data: { username: 'e2e-member', password: 'not-a-real-secret' },
      }),
    )
    credentialId = credential.id

    await page.goto(`/marketplace/${itemId}`)
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
    await page.getByRole('button', { name: '설치', exact: true }).click()
    let dialog = page.getByRole('dialog', { name: `${name} 설치` })
    await expect(dialog).toBeVisible()
    await dialog.getByRole('button', { name: '다음', exact: true }).click()
    await expect(dialog.getByRole('listitem').filter({ hasText: 'SRT 계정' })).toBeVisible()
    await expect(dialog.getByText(/연결하지 않으면 설치 후/)).toBeVisible()
    await dialog.getByRole('button', { name: '다음', exact: true }).click()
    const needsSetupResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/marketplace/items/${itemId}/install`) &&
        response.request().method() === 'POST',
    )
    await dialog.getByRole('button', { name: '설치', exact: true }).click()
    const needsSetup = installationSchema.parse(
      await apiJson(await needsSetupResponse, 'Install marketplace item without credential'),
    )
    expect(needsSetup.install_status).toBe('needs_setup')
    installationId = needsSetup.id
    installedSkillId = needsSetup.installed_skill_id
    await dialog.getByRole('button', { name: '닫기', exact: true }).last().click()

    await page.reload()
    await page.getByRole('button', { name: '설정', exact: true }).click()
    dialog = page.getByRole('dialog', { name: `${name} 설치` })
    const requirement = dialog.getByRole('listitem').filter({ hasText: 'SRT 계정' })
    await expect(requirement).toBeVisible()
    await requirement.getByRole('combobox').click()
    await page.getByRole('option', { name: credentialName, exact: true }).click()
    await dialog.getByRole('button', { name: '다음', exact: true }).click()
    const configuredResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/marketplace/items/${itemId}/install`) &&
        response.request().method() === 'POST',
    )
    await dialog.getByRole('button', { name: '설치', exact: true }).click()
    const configured = installationSchema.parse(
      await apiJson(await configuredResponse, 'Configure marketplace installation'),
    )
    expect(configured.id).toBe(installationId)
    expect(configured.installed_skill_id).toBe(installedSkillId)
    expect(configured.install_status).toBe('active')
    await dialog.getByRole('button', { name: '닫기', exact: true }).last().click()

    await page.reload()
    await expect(page.getByRole('button', { name: '열기', exact: true })).toBeVisible()

    await apiJson(
      await page.request.put(`${API_BASE}/api/skills/${installedSkillId}/files/SKILL.md`, {
        headers: memberCsrf,
        data: {
          content: skillMarkdown(
            slug,
            '1.0.0',
            'Member-owned edits that must not be lost silently.',
          ),
        },
      }),
      'Edit installed marketplace skill',
    )

    await apiJson(
      await request.put(`${API_BASE}/api/skills/${sourceSkillId}/files/SKILL.md`, {
        headers: publisherCsrf,
        data: {
          content: skillMarkdown(slug, '2.0.0', 'Publisher version two instructions.'),
        },
      }),
      'Edit source marketplace skill',
    )
    await apiJson(
      await request.patch(`${API_BASE}/api/skills/${sourceSkillId}`, {
        headers: publisherCsrf,
        data: { version: '2.0.0' },
      }),
      'Version source marketplace skill',
    )
    const updatedItem = marketplaceItemSchema.parse(
      await apiPostJson(
        request,
        `${API_BASE}/api/marketplace/items/${itemId}/versions/from-skill/${sourceSkillId}`,
        publisherCsrf,
        { release_notes: 'Version two lifecycle fixture.' },
      ),
    )
    expect(updatedItem.latest_version.version_label).toBe('2.0.0')

    await page.reload()
    await expect(page.getByRole('button', { name: '업데이트 검토', exact: true })).toBeVisible()
    await page.getByRole('button', { name: '업데이트 검토', exact: true }).click()
    const updateDialog = page.getByRole('dialog', { name: `${name} 업데이트` })
    const safeDefault = updateDialog.getByRole('radio', { name: /Install as a new copy/ })
    await expect(safeDefault).toBeChecked()
    await updateDialog.getByRole('radio', { name: /Overwrite my edits/ }).check()
    const updateResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/api/marketplace/installations/${installationId}/update`) &&
        response.request().method() === 'POST',
    )
    await updateDialog.getByRole('button', { name: '확인', exact: true }).click()
    const overwritten = installationSchema.parse(
      await apiJson(await updateResponse, 'Overwrite dirty marketplace installation'),
    )
    expect(overwritten.installed_skill_id).toBe(installedSkillId)
    expect(overwritten.version_id).toBe(updatedItem.latest_version.id)
    expect(overwritten.is_dirty).toBe(false)

    await page.reload()
    await expect(page.getByRole('button', { name: '열기', exact: true })).toBeVisible()
    const finalItem = marketplaceItemSchema.parse(
      await apiGetJson(page.request, `${API_BASE}/api/marketplace/items/${itemId}`),
    )
    expect(finalItem.installation).toMatchObject({
      installation_id: installationId,
      installed_resource_id: installedSkillId,
      status: 'active',
      update_available: false,
      dirty: false,
    })
    expect(errors.console).toEqual([])
    expect(errors.network).toEqual([])
  } finally {
    if (installationId && memberCsrf) {
      await apiDeleteOk(
        page.request,
        `${API_BASE}/api/marketplace/installations/${installationId}?delete_resource=true`,
        memberCsrf,
      )
    }
    if (credentialId && memberCsrf) {
      await apiDeleteOk(page.request, `${API_BASE}/api/credentials/${credentialId}`, memberCsrf)
    }
    if (itemId) {
      await apiPostJson(
        request,
        `${API_BASE}/api/marketplace/items/${itemId}/disable`,
        publisherCsrf,
      )
    }
    if (sourceSkillId) {
      await apiDeleteOk(request, `${API_BASE}/api/skills/${sourceSkillId}`, publisherCsrf)
    }
  }
})
