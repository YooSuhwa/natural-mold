import { dirname, isAbsolute, relative, sep } from "node:path"
import { realpathSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from "node:url"
import type { NextConfig } from "next"
import createNextIntlPlugin from 'next-intl/plugin'

import { getE2ERunPaths } from './scripts/e2e-lane-contract.mjs'

const withNextIntl = createNextIntlPlugin('./src/i18n/request.ts')
const frontendRoot = dirname(fileURLToPath(import.meta.url))
const workspaceRoot = dirname(frontendRoot)
const e2eLane = process.env.E2E_LANE ?? 'scripted'
const runPaths = getE2ERunPaths(e2eLane, process.env)
const isolatedFrontendRoot = process.env.MOLDY_TEST_RUN_ROOT
  ? realpathSync(join(process.env.MOLDY_TEST_RUN_ROOT, 'frontend'))
  : undefined

if (
  isolatedFrontendRoot &&
  (realpathSync(frontendRoot) !== isolatedFrontendRoot || realpathSync(process.cwd()) !== isolatedFrontendRoot)
) {
  throw new Error('Isolated Next commands must run from the prepared frontend mirror.')
}
const nextDistDir = process.env.MOLDY_TEST_RUN_ROOT
  ? relative(frontendRoot, runPaths.buildDir)
  : runPaths.buildDir

if (isAbsolute(nextDistDir) || nextDistDir === '..' || nextDistDir.startsWith(`..${sep}`)) {
  throw new Error('Next build directory must remain within the frontend project.')
}

const nextConfig: NextConfig = {
  ...(isolatedFrontendRoot ? {} : { output: "standalone" }),
  distDir: nextDistDir,
  allowedDevOrigins: ['127.0.0.1'],
  turbopack: {
    root: workspaceRoot,
  },
}

export default withNextIntl(nextConfig)
