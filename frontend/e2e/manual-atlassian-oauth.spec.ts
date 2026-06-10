import { test, expect } from './fixtures'
import type { Page } from '@playwright/test'
import { execFile } from 'node:child_process'
import fs from 'node:fs/promises'
import path from 'node:path'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)

const OUTPUT_DIR = path.resolve(
  process.cwd(),
  '..',
  'output',
  'e2e-captures',
  '20260608-atlassian-mcp-oauth',
)
const MANUAL_TIMEOUT_MS = Number(process.env.MANUAL_ATLASSIAN_E2E_TIMEOUT_MS ?? '900000')
const QUERY = process.env.ATLASSIAN_MCP_E2E_QUERY ?? 'Moldy'
const CLOUD_ID = process.env.ATLASSIAN_MCP_E2E_CLOUD_ID
const CREDENTIAL_NAME = `Atlassian Rovo OAuth ${Date.now()}`

type ExecFileError = Error & {
  stdout?: unknown
  stderr?: unknown
}

async function capture(page: Page, name: string): Promise<void> {
  await fs.mkdir(OUTPUT_DIR, { recursive: true })
  await page.screenshot({
    path: path.join(OUTPUT_DIR, name),
    fullPage: true,
  })
}

async function runVerifier(): Promise<void> {
  const backendDir = path.resolve(process.cwd(), '..', 'backend')
  const args = ['run', 'python', 'scripts/verify_atlassian_mcp_access.py', '--query', QUERY]
  if (CLOUD_ID) {
    args.push('--cloud-id', CLOUD_ID)
  }

  try {
    const { stdout, stderr } = await execFileAsync('uv', args, {
      cwd: backendDir,
      env: process.env,
      timeout: 180_000,
      maxBuffer: 10 * 1024 * 1024,
    })
    await fs.writeFile(path.join(OUTPUT_DIR, 'atlassian-mcp-verification.json'), stdout)
    if (stderr.trim()) {
      await fs.writeFile(path.join(OUTPUT_DIR, 'atlassian-mcp-verification.stderr.txt'), stderr)
    }
    expect(stdout).toContain('"success": true')
  } catch (error) {
    const execError = error as ExecFileError
    const detail =
      error instanceof Error && ('stdout' in execError || 'stderr' in execError)
        ? `${error.message}\n${String(execError.stdout ?? '')}\n${String(execError.stderr ?? '')}`
        : error instanceof Error
          ? error.message
          : String(error)
    throw new Error(`Atlassian MCP verification failed: ${detail}`)
  }
}

test.describe('Manual Atlassian Rovo MCP OAuth', () => {
  test.describe.configure({ retries: 0 })

  test.skip(
    process.env.MANUAL_ATLASSIAN_E2E !== '1',
    'Set MANUAL_ATLASSIAN_E2E=1 to run the headed Atlassian OAuth flow.',
  )
  test.skip(process.env.PW_SKIP_BACKEND === '1', 'Requires the FastAPI backend')

  test('connects Atlassian OAuth, saves the MCP server, and verifies document access', async ({
    page,
  }) => {
    test.setTimeout(MANUAL_TIMEOUT_MS)

    console.log('Opening MCP server wizard')
    await page.goto('/mcp-servers')
    await page
      .getByRole('button', { name: /새 MCP 서버|서버 추가/ })
      .first()
      .click()
    await expect(page.getByText('빠른 시작')).toBeVisible()
    await capture(page, '01-mcp-wizard-registry.png')

    const atlassianCard = page.getByTestId('registry-card-atlassian-rovo')
    if ((await atlassianCard.count()) > 0) {
      await atlassianCard.click()
    } else {
      await page.getByTestId('registry-card-jira').click()
    }

    console.log('Selected Atlassian Rovo registry entry')
    await expect(page.getByLabel('이름')).toHaveValue(/Atlassian|Jira/)
    await page.getByRole('button', { name: '인증으로 계속 →' }).click()
    await expect(page.getByText('mcp_oauth2 타입 자격증명으로 필터링되었습니다.')).toBeVisible()
    await capture(page, '02-auth-tab-oauth-actions.png')

    console.log('Creating MCP OAuth credential')
    await page.getByRole('button', { name: 'OAuth 자격증명 만들기' }).click()
    const credentialDialog = page.getByRole('dialog').filter({ hasText: /MCP OAuth2/i }).last()
    await expect(credentialDialog).toBeVisible()
    await expect(credentialDialog.getByLabel('이름')).toHaveValue(/OAuth/)
    await credentialDialog.getByLabel('이름').fill(CREDENTIAL_NAME)
    await capture(page, '03-oauth-credential-prefilled.png')
    await credentialDialog.getByRole('button', { name: '저장' }).click()
    await expect(page.getByRole('button', { name: '브라우저로 인증' })).toBeVisible({
      timeout: 30_000,
    })

    console.log('Selecting the newly created credential')
    await page.getByRole('combobox').click()
    await page.getByRole('option', { name: new RegExp(CREDENTIAL_NAME) }).last().click()
    await capture(page, '04-credential-selected.png')
    await expect(page.getByRole('button', { name: '브라우저로 인증' })).toBeEnabled({
      timeout: 10_000,
    })

    console.log('Starting OAuth popup')
    const popupPromise = page.waitForEvent('popup')
    await page.getByRole('button', { name: '브라우저로 인증' }).click()
    const popup = await popupPromise
    await popup.waitForLoadState('domcontentloaded')
    await expect
      .poll(() => popup.url(), { timeout: 30_000 })
      .toMatch(/atlassian|oauth|authorize/i)
    await capture(popup, '04-atlassian-login-or-consent.png')

    console.log(
      [
        '',
        'Complete the Atlassian login/consent in the popup.',
        'The test continues after the Moldy OAuth callback closes the popup.',
        '',
      ].join('\n'),
    )

    await expect
      .poll(async () => {
        if (popup.isClosed()) return 'closed'
        const statusVisible = await page
          .getByText('OAuth 인증이 완료되었습니다.')
          .isVisible()
          .catch(() => false)
        return statusVisible ? 'connected' : popup.url()
      }, { timeout: MANUAL_TIMEOUT_MS })
      .toMatch(/closed|connected/)

    await expect(page.getByText('OAuth 인증이 완료되었습니다.').last()).toBeVisible({
      timeout: 30_000,
    })
    await capture(page, '05-oauth-complete.png')

    await page.getByRole('button', { name: '연결 테스트' }).click()
    await expect(page.getByText(/연결됨|개 도구를 찾았습니다/)).toBeVisible({
      timeout: 120_000,
    })
    await page.getByRole('button', { name: '도구로 계속 →' }).click()
    await expect(page.getByText(/개 도구 발견됨/)).toBeVisible({ timeout: 120_000 })
    await capture(page, '06-tools-discovered.png')

    await page.getByRole('button', { name: '서버 저장' }).click()
    await expect(page.getByText(/Atlassian|Jira/).first()).toBeVisible({ timeout: 30_000 })
    await capture(page, '07-server-saved.png')

    await runVerifier()
  })
})
