import { request } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { getE2EAuthStatePath } from '../scripts/e2e-lane-contract.mjs'

const dirname = path.dirname(fileURLToPath(import.meta.url))
const e2eLane = process.env.E2E_LANE ?? 'scripted'
const authFile = path.resolve(process.cwd(), getE2EAuthStatePath(e2eLane, process.env))
const repoRoot = path.resolve(dirname, '..', '..')
const requiredSkillNodePackages = ['docx', 'xlsx', 'pptxgenjs']

const backendPort = process.env.E2E_BACKEND_PORT ?? '8101'
const apiBase = process.env.E2E_API_BASE_URL ?? `http://localhost:${backendPort}`
const email = process.env.E2E_USER_EMAIL ?? process.env.E2E_EMAIL ?? 'playwright-e2e@moldy.dev'
const password =
  process.env.E2E_USER_PASSWORD ?? process.env.E2E_PASSWORD ?? 'correct horse battery staple 42'
const name = process.env.E2E_USER_NAME ?? process.env.E2E_NAME ?? 'E2E User'

export function authenticationSetupFailure(label, status) {
  return new Error(`${label} failed (${status}).`)
}

export function resolveSkillNodeModulesDirectory(environment, repositoryRoot = repoRoot) {
  if (!environment.MOLDY_TEST_RUN_ROOT) {
    return path.join(repositoryRoot, 'backend', 'skill-node', 'node_modules')
  }
  const backendSourceRoot = environment.MOLDY_BACKEND_SOURCE_ROOT
  if (!backendSourceRoot) {
    throw new Error('MOLDY_BACKEND_SOURCE_ROOT is required in isolated E2E mode.')
  }
  if (!path.isAbsolute(backendSourceRoot)) {
    throw new Error('MOLDY_BACKEND_SOURCE_ROOT must be absolute in isolated E2E mode.')
  }
  return path.join(path.resolve(backendSourceRoot), 'skill-node', 'node_modules')
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
  const skillNodeModules = resolveSkillNodeModulesDirectory(process.env)
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
      throw authenticationSetupFailure('E2E authentication setup', response.status())
    }

    await api.storageState({ path: authFile })
  } finally {
    await api.dispose()
  }
}
