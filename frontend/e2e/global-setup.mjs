import { request } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const dirname = path.dirname(fileURLToPath(import.meta.url))
const authFile = path.join(dirname, '.auth', 'user.json')
const repoRoot = path.resolve(dirname, '..', '..')
const skillNodeModules = path.join(repoRoot, 'backend', 'skill-node', 'node_modules')
const requiredSkillNodePackages = ['docx', 'xlsx', 'pptxgenjs']

const backendPort = process.env.E2E_BACKEND_PORT ?? '8001'
const apiBase = process.env.E2E_API_BASE_URL ?? `http://localhost:${backendPort}`
const email = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const password =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'
const name = process.env.E2E_USER_NAME ?? process.env.E2E_NAME ?? 'E2E User'

async function failWithBody(label, response) {
  const body = await response.text().catch(() => '')
  throw new Error(`${label} failed (${response.status()}): ${body.slice(0, 500)}`)
}

async function writeSkipBackendState() {
  await fs.writeFile(
    authFile,
    JSON.stringify(
      {
        cookies: [
          {
            name: 'moldy_rt',
            value: 'skip-backend-refresh-token',
            domain: 'localhost',
            path: '/',
            expires: -1,
            httpOnly: true,
            secure: false,
            sameSite: 'Lax',
          },
          {
            name: 'moldy_csrf',
            value: 'skip-backend-csrf-token',
            domain: 'localhost',
            path: '/',
            expires: -1,
            httpOnly: false,
            secure: false,
            sameSite: 'Lax',
          },
        ],
        origins: [],
      },
      null,
      2,
    ),
  )
}

async function assertSkillNodeDependencies() {
  const missing = []
  for (const packageName of requiredSkillNodePackages) {
    try {
      await fs.access(path.join(skillNodeModules, packageName))
    } catch {
      missing.push(packageName)
    }
  }
  if (missing.length > 0) {
    throw new Error(
      [
        `Missing backend skill-node dependencies: ${missing.join(', ')}`,
        'Run `pnpm install --frozen-lockfile` from the repository root before full E2E.',
        'These packages are required by execute_in_skill document artifact tests.',
      ].join('\n'),
    )
  }
}

export default async function globalSetup() {
  await fs.mkdir(path.dirname(authFile), { recursive: true })

  if (process.env.PW_SKIP_BACKEND === '1') {
    await writeSkipBackendState()
    return
  }

  await assertSkillNodeDependencies()

  const auth = { email, password }
  const api = await request.newContext({ baseURL: apiBase })

  try {
    let response = await api.post('/api/auth/login', { data: auth })
    if (!response.ok()) {
      response = await api.post('/api/auth/register', {
        data: { ...auth, name },
      })
    }
    if (!response.ok() && response.status() === 409) {
      response = await api.post('/api/auth/login', { data: auth })
    }
    if (!response.ok()) {
      await failWithBody('E2E authentication setup', response)
    }

    await api.storageState({ path: authFile })
  } finally {
    await api.dispose()
  }
}
