#!/usr/bin/env node
// Run one journey against a verify-moldy run and capture evidence.
//   node drive.mjs <journey.mjs | journey-name> [--run <run-id>] [--headed]
// A journey is an ES module: `export default async function (ctx) {...}` and an
// optional `export const options = { loggedOut: true }`.
// ctx = { page, context, api, state, step, expect, scriptedModelId, appUrl, apiUrl }
//   step(name)      -> screenshot + ARIA snapshot + log line, numbered in order
//   api.get/post/patch/delete(path, data?) -> JSON (CSRF header added for mutations)
// Evidence: .verify-moldy/runs/<id>/evidence/<journey>-<time>/ (result.json, NN-*.png,
// NN-*.aria.yml, steps.jsonl, console.jsonl, trace.zip). Exit 0 only on pass.
import { createRequire } from 'node:module'
import fs from 'node:fs'
import path from 'node:path'
import { execFileSync } from 'node:child_process'
import { fileURLToPath, pathToFileURL } from 'node:url'

const skillDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const repo = execFileSync('git', ['-C', skillDir, 'rev-parse', '--show-toplevel']).toString().trim()
const require = createRequire(path.join(repo, 'frontend', 'package.json'))
const { chromium, expect } = require('@playwright/test')

const args = process.argv.slice(2)
const flag = (name) => {
  const i = args.indexOf(name)
  if (i === -1) return undefined
  const value = args[i + 1]
  args.splice(i, 2)
  return value
}
const headed = args.includes('--headed') ? (args.splice(args.indexOf('--headed'), 1), true) : false
const runId = flag('--run')
const journeyArg = args[0]
if (!journeyArg) {
  console.error('usage: drive.mjs <journey.mjs|name> [--run <run-id>] [--headed]')
  process.exit(64)
}

const runDir = runId
  ? path.join(repo, '.verify-moldy', 'runs', runId)
  : fs.realpathSync(path.join(repo, '.verify-moldy', 'current'))
const state = Object.fromEntries(
  execFileSync('bash', ['-c', 'set -a; source "$1"; env -0',
    'verify-moldy-state', path.join(runDir, 'state.env')], { env: { PATH: process.env.PATH } })
    .toString()
    .split('\0')
    .filter((line) => line.includes('='))
    .map((line) => {
      const i = line.indexOf('=')
      return [line.slice(0, i), line.slice(i + 1)]
    }),
)
if (fs.existsSync(path.join(runDir, 'CLEANED'))) {
  console.error(`run ${state.RUN_ID} was cleaned up; start a new one with up.sh`)
  process.exit(2)
}

const journeyPath = fs.existsSync(journeyArg)
  ? path.resolve(journeyArg)
  : path.join(skillDir, 'journeys', `${journeyArg.replace(/\.mjs$/, '')}.mjs`)
const journeyName = path.basename(journeyPath, '.mjs')
const journey = await import(pathToFileURL(journeyPath).href)
const stamp = new Date().toISOString().replace(/[:.]/g, '-')
const outDir = path.join(runDir, 'evidence', `${journeyName}-${stamp}`)
fs.mkdirSync(outDir, { recursive: true })
fs.writeFileSync(path.join(outDir, 'console.jsonl'), '')
const appendJsonl = (file, record) =>
  fs.appendFileSync(path.join(outDir, file), `${JSON.stringify(record)}\n`)

const browser = await chromium.launch({ headless: !headed })
const context = await browser.newContext({
  baseURL: state.APP_URL,
  locale: 'ko-KR',
  timezoneId: 'Asia/Seoul',
  viewport: { width: 1440, height: 900 },
})
await context.tracing.start({ screenshots: true, snapshots: true })
// Same dismissals the E2E suite uses so onboarding/welcome overlays do not cover the app.
await context.addInitScript(() => {
  window.sessionStorage.setItem('moldy.onboarding_dismissed', '1')
  window.sessionStorage.setItem('moldy.super_user_welcomed', '1')
})

let csrf = ''
async function apiCall(method, urlPath, data) {
  const response = await context.request.fetch(`${state.API_URL}${urlPath}`, {
    method,
    data,
    headers: method === 'GET' ? {} : { 'X-CSRF-Token': csrf },
  })
  const text = await response.text()
  if (!response.ok()) throw new Error(`${method} ${urlPath} -> ${response.status()} ${text.slice(0, 300)}`)
  return text ? JSON.parse(text) : null
}
const api = {
  get: (p) => apiCall('GET', p),
  post: (p, d) => apiCall('POST', p, d ?? {}),
  patch: (p, d) => apiCall('PATCH', p, d ?? {}),
  delete: (p) => apiCall('DELETE', p),
}
async function apiLogin() {
  const response = await context.request.post(`${state.API_URL}/api/auth/login`, {
    data: { email: state.USER_EMAIL, password: state.USER_PASSWORD },
  })
  if (!response.ok()) throw new Error(`API login failed: ${response.status()}`)
  csrf = (await response.json()).csrf_token
}
if (!journey.options?.loggedOut) await apiLogin()

const page = await context.newPage()
page.on('console', (msg) => {
  if (msg.type() === 'error' || msg.type() === 'warning')
    appendJsonl('console.jsonl', { type: msg.type(), text: msg.text(), url: page.url() })
})
page.on('pageerror', (err) => appendJsonl('console.jsonl', { type: 'pageerror', text: String(err) }))
page.on('response', (res) => {
  if (res.status() >= 500) appendJsonl('console.jsonl', { type: 'http5xx', status: res.status(), url: res.url() })
})

let stepNo = 0
async function step(name, note = '') {
  stepNo += 1
  const base = `${String(stepNo).padStart(2, '0')}-${name.replace(/[^a-z0-9-]+/gi, '-')}`
  await page.screenshot({ path: path.join(outDir, `${base}.png`), fullPage: false, caret: 'initial' })
  const aria = await page.locator('body').ariaSnapshot().catch((e) => `ariaSnapshot failed: ${e}`)
  fs.writeFileSync(path.join(outDir, `${base}.aria.yml`), aria)
  appendJsonl('steps.jsonl', { step: stepNo, name, note, url: page.url(), at: new Date().toISOString() })
  console.log(`  step ${stepNo}: ${name} ${note}`)
}
async function scriptedModelId() {
  const models = await api.get('/api/models')
  const model = models.find((m) => m.provider === 'e2e_scripted' && m.model_name === 'document-artifact-scripted')
  if (!model) throw new Error('scripted model not seeded')
  return model.id
}

const result = { journey: journeyName, run: state.RUN_ID, rev: state.GIT_REV, app: state.APP_URL, startedAt: new Date().toISOString() }
let exitCode = 0
console.log(`drive ${journeyName} on run ${state.RUN_ID} (${state.APP_URL})`)
try {
  const ctx = { page, context, api, state, step, expect, scriptedModelId, appUrl: state.APP_URL, apiUrl: state.API_URL, outDir, apiLogin }
  result.output = (await journey.default(ctx)) ?? null
  result.status = 'pass'
} catch (error) {
  exitCode = 1
  result.status = 'fail'
  result.error = String(error?.stack ?? error)
  await step('failure').catch(() => {})
} finally {
  result.finishedAt = new Date().toISOString()
  await context.tracing.stop({ path: path.join(outDir, 'trace.zip') }).catch(() => {})
  await browser.close()
  fs.writeFileSync(path.join(outDir, 'result.json'), JSON.stringify(result, null, 2))
}
console.log(`${result.status.toUpperCase()}  evidence: ${outDir}`)
if (result.error) console.error(result.error)
process.exit(exitCode)
