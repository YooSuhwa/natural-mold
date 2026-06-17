import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { cwd, exit, version } from 'node:process'

const root = cwd()
const requiredNodeMajor = '22'
const currentMajor = version.replace(/^v/, '').split('.')[0]

const checks = [
  {
    name: 'node-major',
    ok: currentMajor === requiredNodeMajor,
    message: `Expected Node ${requiredNodeMajor}.x from ../.node-version, got ${version}.`,
  },
  {
    name: 'node-modules',
    ok: existsSync(join(root, 'node_modules')),
    message: 'Missing frontend/node_modules. Run `pnpm install --frozen-lockfile` from frontend.',
  },
  {
    name: 'rhwp-wasm',
    ok: existsSync(join(root, 'node_modules', '@rhwp', 'core', 'rhwp_bg.wasm')),
    message:
      'Missing node_modules/@rhwp/core/rhwp_bg.wasm. Reinstall dependencies before build/dev.',
  },
  {
    name: 'messages-ko',
    ok: existsSync(join(root, 'messages', 'ko.json')),
    message: 'Missing messages/ko.json.',
  },
  {
    name: 'messages-en',
    ok: existsSync(join(root, 'messages', 'en.json')),
    message: 'Missing messages/en.json.',
  },
]

let failed = false

for (const check of checks) {
  if (check.ok) {
    console.log(`ok ${check.name}`)
  } else {
    failed = true
    console.error(`fail ${check.name}: ${check.message}`)
  }
}

if (failed) exit(1)
