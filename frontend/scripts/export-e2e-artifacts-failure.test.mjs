import { spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { classifyExportFailure, E2E_EXPORT_FAILURE_CATEGORIES } from './export-e2e-artifacts.mjs'

const SCRIPT = path.join(path.dirname(fileURLToPath(import.meta.url)), 'export-e2e-artifacts.mjs')
const PREFIX = 'E2E_ARTIFACT_EXPORT_FAILURE:'

function fixture() {
  const repositoryRoot = mkdtempSync(path.join(os.tmpdir(), 'moldy-export-failure-'))
  const runRoot = path.join(repositoryRoot, '.moldy-test-run.sensitive-private-run')
  const results = path.join(runRoot, 'frontend/test-results/scripted-full')
  mkdirSync(results, { recursive: true, mode: 0o700 })
  mkdirSync(path.join(runRoot, 'output/captures'), { recursive: true, mode: 0o700 })
  return { repositoryRoot, runRoot, results }
}

function writeArtifact(root, relative, content) {
  const target = path.join(root, relative)
  mkdirSync(path.dirname(target), { recursive: true, mode: 0o700 })
  writeFileSync(target, content)
}

function runCli(fixture_, overrides = {}) {
  const source = overrides.source ?? fixture_.results
  const repositoryRoot = overrides.repositoryRoot ?? fixture_.repositoryRoot
  return spawnSync(
    process.execPath,
    [
      SCRIPT,
      '--run-root',
      fixture_.runRoot,
      '--source-dir',
      source,
      '--project',
      'scripted-full',
      '--slug',
      'failure-contract',
      '--repo-root',
      repositoryRoot,
    ],
    {
      encoding: 'utf8',
      env: {
        ...process.env,
        NODE_OPTIONS: '',
        E2E_EXPORT_SECRETS_JSON: overrides.secrets ?? '[]',
      },
    },
  )
}

function expectFailure(result, category, sensitiveValues) {
  expect(result.status).toBe(1)
  expect(result.stdout).toBe('')
  expect(result.stderr).toBe(`${PREFIX}${category}\n`)
  for (const sensitive of sensitiveValues) expect(result.stderr).not.toContain(sensitive)
}

function expectNoPartialExport(fixture_) {
  const parent = path.join(fixture_.repositoryRoot, 'output/e2e-captures')
  try {
    expect(readdirSync(parent)).toEqual([])
  } catch (error) {
    if (!(error instanceof Error) || !('code' in error) || error.code !== 'ENOENT') throw error
  }
}

describe('E2E artifact exporter failure contract', () => {
  it('emits a sanitized source_topology category', () => {
    const fixture_ = fixture()
    const outside = mkdtempSync(path.join(os.tmpdir(), 'customer-secret-source-'))
    try {
      expectFailure(runCli(fixture_, { source: outside }), 'source_topology', [
        outside,
        'customer-secret',
      ])
      expectNoPartialExport(fixture_)
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
      rmSync(outside, { recursive: true, force: true })
    }
  })

  it('emits a sanitized unsupported_artifact category', () => {
    const fixture_ = fixture()
    try {
      writeArtifact(fixture_.results, 'case/customer-secret-name.json', '{"safe":true}')
      expectFailure(runCli(fixture_), 'unsupported_artifact', [
        'customer-secret-name',
        fixture_.runRoot,
      ])
      expectNoPartialExport(fixture_)
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('emits a sanitized secret_scan category', () => {
    const fixture_ = fixture()
    try {
      writeArtifact(fixture_.results, 'junit.xml', 'password=super-sensitive-value')
      expectFailure(runCli(fixture_), 'secret_scan', ['super-sensitive-value', fixture_.runRoot])
      expectNoPartialExport(fixture_)
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('emits a sanitized bounds category', () => {
    const fixture_ = fixture()
    try {
      const values = Array.from({ length: 129 }, (_, index) => `private-value-${index}`)
      expectFailure(runCli(fixture_, { secrets: JSON.stringify(values) }), 'bounds', [
        'private-value',
      ])
      expectNoPartialExport(fixture_)
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('emits a sanitized manifest_publish category', () => {
    const fixture_ = fixture()
    const invalidRoot = path.join(fixture_.repositoryRoot, 'customer-secret-repository-file')
    try {
      writeFileSync(invalidRoot, 'not a directory')
      expectFailure(runCli(fixture_, { repositoryRoot: invalidRoot }), 'manifest_publish', [
        invalidRoot,
        'customer-secret',
      ])
      expectNoPartialExport(fixture_)
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('collapses arbitrary exceptions to the stable internal category', () => {
    expect(E2E_EXPORT_FAILURE_CATEGORIES).toEqual([
      'source_topology',
      'unsupported_artifact',
      'secret_scan',
      'bounds',
      'manifest_publish',
      'internal',
    ])
    expect(
      classifyExportFailure(new Error('/private/path customer-secret arbitrary failure')),
    ).toBe('internal')
    expect(classifyExportFailure('customer-secret non-error')).toBe('internal')
  })
})
