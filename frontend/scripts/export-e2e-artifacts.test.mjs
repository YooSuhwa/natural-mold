import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import {
  existsSync,
  linkSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  realpathSync,
  readdirSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import JSZip from 'jszip'
import { describe, expect, it } from 'vitest'

import { exportE2EArtifacts } from './export-e2e-artifacts.mjs'

function fixture() {
  const repositoryRoot = mkdtempSync(path.join(os.tmpdir(), 'moldy-export-repository-'))
  const runRoot = path.join(repositoryRoot, '.moldy-test-run.fixture')
  const captures = path.join(runRoot, 'output', 'captures')
  const legacyCaptures = path.join(runRoot, 'output', 'e2e-captures')
  for (const relative of [
    'frontend',
    'frontend/test-results/scripted-smoke',
    'frontend/test-results/scripted-full',
    'frontend/test-results/scripted-capture',
    'frontend/test-results/live-manual',
    'output/captures',
    'output/e2e-captures',
  ])
    mkdirSync(path.join(runRoot, relative), { recursive: true, mode: 0o700 })
  return { repositoryRoot, runRoot, captures, legacyCaptures }
}

function results(fixture_, project) {
  return path.join(fixture_.runRoot, 'frontend', 'test-results', project)
}

function writeArtifact(root, relative, value = 'safe artifact') {
  const destination = path.join(root, relative)
  mkdirSync(path.dirname(destination), { recursive: true, mode: 0o700 })
  writeFileSync(destination, value)
  return destination
}

function exportFixture(fixture_, overrides = {}) {
  const project = overrides.project ?? 'scripted-full'
  return exportE2EArtifacts({
    environment: { E2E_EXPORT_SLUG: 'artifact-export' },
    repositoryRoot: fixture_.repositoryRoot,
    runRoot: fixture_.runRoot,
    sourceDirectory: results(fixture_, project),
    project,
    now: new Date('2026-08-31T00:15:00.000Z'),
    ...overrides,
  })
}

function removeExport(fixture_, receipt) {
  rmSync(path.join(fixture_.repositoryRoot, receipt.export_directory), { recursive: true })
}

function withSwap(phase, target, action) {
  const replacement = `${target}-attacker`
  const saved = `${target}-saved`
  mkdirSync(replacement, { recursive: true, mode: 0o700 })
  writeFileSync(path.join(replacement, 'attacker-marker.txt'), 'must remain untouched')
  const previous = process.env.MOLDY_E2E_FS_TEST_SWAP_JSON
  process.env.MOLDY_E2E_FS_TEST_SWAP_JSON = JSON.stringify({
    phase,
    target,
    replacement,
    saved,
  })
  try {
    return action({ replacement, saved })
  } finally {
    if (previous === undefined) delete process.env.MOLDY_E2E_FS_TEST_SWAP_JSON
    else process.env.MOLDY_E2E_FS_TEST_SWAP_JSON = previous
  }
}

describe('E2E artifact exporter', () => {
  it('writes a hash-verifiable manifest and compatible receipt through the CLI', () => {
    const fixture_ = fixture()
    try {
      writeArtifact(results(fixture_, 'scripted-smoke'), 'junit.xml', '<testsuite/>')
      const receiptPath = path.join(fixture_.runRoot, 'receipts', 'export.json')
      const result = spawnSync(
        process.execPath,
        [
          path.join(path.dirname(fileURLToPath(import.meta.url)), 'export-e2e-artifacts.mjs'),
          '--run-root',
          fixture_.runRoot,
          '--source-dir',
          results(fixture_, 'scripted-smoke'),
          '--project',
          'scripted-smoke',
          '--slug',
          'cli-export',
          '--repo-root',
          fixture_.repositoryRoot,
          '--receipt',
          receiptPath,
        ],
        { encoding: 'utf8', env: { ...process.env, E2E_EXPORT_SECRETS_JSON: '["not-present"]' } },
      )

      expect(result.status, result.stderr).toBe(0)
      const receipt = JSON.parse(result.stdout)
      expect(receipt).toEqual(JSON.parse(readFileSync(receiptPath, 'utf8')))
      const exportRoot = path.join(fixture_.repositoryRoot, receipt.export_directory)
      const manifestBytes = readFileSync(path.join(exportRoot, receipt.manifest.path))
      expect(createHash('sha256').update(manifestBytes).digest('hex')).toBe(receipt.manifest.sha256)
      expect(manifestBytes.length).toBe(receipt.manifest.size_bytes)
      expect(JSON.parse(manifestBytes).project).toBe('scripted-smoke')
      expect(receipt.files).toContainEqual(receipt.manifest)
      expect(result.stdout).not.toContain(fixture_.runRoot)
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('exports explicit result artifacts and arbitrary safe capture directories for scripted-capture', async () => {
    const fixture_ = fixture()
    try {
      const resultDirectory = results(fixture_, 'scripted-capture')
      writeArtifact(resultDirectory, 'junit.xml', '<testsuite/>')
      writeArtifact(resultDirectory, 'playwright-artifacts/.last-run.json', '{"status":"passed"}')
      writeArtifact(resultDirectory, 'selection.json', '{"passed":true}')
      writeArtifact(resultDirectory, 'execution.stderr.log', 'safe process stderr')
      writeArtifact(
        resultDirectory,
        'playwright-artifacts/suite-case/error-context.md',
        '# safe page snapshot',
      )
      writeArtifact(
        resultDirectory,
        'playwright-artifacts/suite-case/test-failed-1.png',
        'failure screenshot',
      )
      const archive = new JSZip()
      archive.file('trace.network', '{"type":"safe"}')
      writeArtifact(
        resultDirectory,
        'playwright-artifacts/suite-case/trace.zip',
        await archive.generateAsync({ type: 'nodebuffer' }),
      )
      writeArtifact(fixture_.captures, 'wave-7/dashboard/dashboard.png', 'canonical capture')
      writeArtifact(fixture_.legacyCaptures, '20260831-feature/legacy.png', 'legacy capture')

      const receipt = exportFixture(fixture_, {
        project: 'scripted-capture',
        sourceDirectories: [resultDirectory, fixture_.captures, fixture_.legacyCaptures],
      })

      expect(receipt.files.map((file) => file.path)).toEqual([
        'export-manifest.json',
        'captures/wave-7/dashboard/dashboard.png',
        'legacy-captures/20260831-feature/legacy.png',
        'results/execution.stderr.log',
        'results/junit.xml',
        'results/playwright-artifacts/suite-case/error-context.md',
        'results/playwright-artifacts/suite-case/test-failed-1.png',
        'results/playwright-artifacts/suite-case/trace.zip',
        'results/selection.json',
      ])
      expect(receipt.screenshots).toHaveLength(3)
      const manifest = JSON.parse(
        readFileSync(
          path.join(fixture_.repositoryRoot, receipt.export_directory, receipt.manifest.path),
        ),
      )
      expect(manifest).toMatchObject({
        schema_version: 1,
        project: 'scripted-capture',
        policy: { version: 1, screenshots: 'scripted-capture-only' },
        secret_scan: { passed: true, exact_secret_count: 0 },
        total: { file_count: 8 },
      })
      expect(manifest.total.size_bytes).toBe(
        manifest.files.reduce((sum, file) => sum + file.size_bytes, 0),
      )
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('exports scripted-full failure context while ignoring its Playwright failure screenshot', () => {
    const fixture_ = fixture()
    try {
      const resultDirectory = results(fixture_, 'scripted-full')
      writeArtifact(resultDirectory, 'playwright-artifacts/suite-case/error-context.md')
      writeArtifact(
        resultDirectory,
        'playwright-artifacts/suite-case/test-failed-1.png',
        'ignored-screenshot-secret',
      )

      const receipt = exportFixture(fixture_, { secrets: ['ignored-screenshot-secret'] })

      expect(receipt.secret_scan_passed).toBe(true)
      expect(receipt.files.map((file) => file.path)).toEqual([
        'export-manifest.json',
        'results/playwright-artifacts/suite-case/error-context.md',
      ])
      expect(receipt.screenshots).toEqual([])
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('exports scripted-full diagnostics without collecting legacy functional screenshots', () => {
    const fixture_ = fixture()
    try {
      const resultDirectory = results(fixture_, 'scripted-full')
      writeArtifact(resultDirectory, 'junit.xml', '<testsuite failures="1"/>')
      writeArtifact(resultDirectory, 'execution.log', 'bounded failure summary')
      writeArtifact(
        fixture_.legacyCaptures,
        '20260615-skill-history/functional-screenshot.png',
        'legacy screenshot outside the selected source',
      )

      const receipt = exportFixture(fixture_, { sourceDirectories: [resultDirectory] })

      expect(receipt.files.map((file) => file.path)).toEqual([
        'export-manifest.json',
        'results/execution.log',
        'results/junit.xml',
      ])
      expect(receipt.screenshots).toEqual([])
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('rejects persistent capture screenshots from smoke, full, and live projects', () => {
    for (const project of ['scripted-smoke', 'scripted-full', 'live-manual']) {
      const fixture_ = fixture()
      try {
        writeArtifact(fixture_.captures, 'wave-1/dashboard.png')
        expect(() =>
          exportFixture(fixture_, { project, sourceDirectory: fixture_.captures }),
        ).toThrow('scripted-capture project')
      } finally {
        rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
      }
    }
  })

  it('fails on artifact-like extras outside the narrow topology and ignores ordinary notes', () => {
    const fixture_ = fixture()
    try {
      const resultDirectory = results(fixture_, 'scripted-full')
      writeArtifact(resultDirectory, 'junit.xml')
      writeArtifact(resultDirectory, 'notes.txt')
      const receipt = exportFixture(fixture_)
      expect(receipt.files.map((file) => file.path)).toEqual([
        'export-manifest.json',
        'results/junit.xml',
      ])
      removeExport(fixture_, receipt)
      writeArtifact(resultDirectory, 'case/report.json')
      expect(() => exportFixture(fixture_)).toThrow('Suspicious E2E artifact')
      rmSync(path.join(resultDirectory, 'case/report.json'))
      writeArtifact(resultDirectory, 'case/execution.stderr.log')
      expect(() => exportFixture(fixture_)).toThrow('Suspicious E2E artifact')
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('excludes Playwright internal dotfiles from a checker-compatible passed smoke receipt', () => {
    const fixture_ = fixture()
    try {
      const resultDirectory = results(fixture_, 'scripted-smoke')
      writeArtifact(resultDirectory, 'junit.xml', '<testsuite/>')
      writeArtifact(resultDirectory, '.last-run.json', '{"status":"passed"}')
      writeArtifact(resultDirectory, 'playwright-artifacts/.last-run.json', '{"status":"passed"}')

      const receipt = exportFixture(fixture_, { project: 'scripted-smoke' })
      const receiptPaths = receipt.files.map((file) => file.path)
      const manifest = JSON.parse(
        readFileSync(
          path.join(fixture_.repositoryRoot, receipt.export_directory, receipt.manifest.path),
        ),
      )

      expect(receiptPaths).toEqual(['export-manifest.json', 'results/junit.xml'])
      expect(receiptPaths).not.toContain('results/.last-run.json')
      expect(receiptPaths).not.toContain('results/playwright-artifacts/.last-run.json')
      expect(manifest.files.map((file) => file.path)).toEqual(['results/junit.xml'])
      expect(manifest.files.map((file) => file.path)).not.toContain(
        'results/playwright-artifacts/.last-run.json',
      )
      expect(() =>
        lstatSync(
          path.join(
            fixture_.repositoryRoot,
            receipt.export_directory,
            'results/playwright-artifacts/.last-run.json',
          ),
        ),
      ).toThrow()
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('rejects every other nested dotfile', () => {
    const fixture_ = fixture()
    try {
      const resultDirectory = results(fixture_, 'scripted-full')
      writeArtifact(resultDirectory, 'playwright-artifacts/.other.json', '{"status":"passed"}')
      expect(() => exportFixture(fixture_)).toThrow('explicit topology policy')
      rmSync(path.join(resultDirectory, 'playwright-artifacts/.other.json'))
      writeArtifact(resultDirectory, 'other/.last-run.json', '{"status":"passed"}')
      expect(() => exportFixture(fixture_)).toThrow('explicit topology policy')
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('rejects traversal, symbolic links, hard links, and oversized regular files', () => {
    const fixture_ = fixture()
    const outside = mkdtempSync(path.join(os.tmpdir(), 'moldy-export-outside-'))
    try {
      writeArtifact(results(fixture_, 'scripted-full'), 'junit.xml')
      expect(() => exportFixture(fixture_, { sourceDirectory: outside })).toThrow(
        'prepared capture subtree',
      )
      symlinkSync(outside, path.join(results(fixture_, 'scripted-full'), 'linked'), 'dir')
      expect(() => exportFixture(fixture_)).toThrow('symbolic links')
      rmSync(path.join(results(fixture_, 'scripted-full'), 'linked'))
      const source = writeArtifact(results(fixture_, 'scripted-full'), 'selection.log')
      linkSync(source, path.join(results(fixture_, 'scripted-full'), 'execution.log'))
      expect(() => exportFixture(fixture_)).toThrow('hard-linked')
      rmSync(path.join(results(fixture_, 'scripted-full'), 'execution.log'))
      rmSync(source)
      writeArtifact(
        results(fixture_, 'scripted-full'),
        'execution.log',
        Buffer.alloc(20 * 1024 * 1024 + 1),
      )
      expect(() => exportFixture(fixture_)).toThrow('size limit')
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
      rmSync(outside, { recursive: true, force: true })
    }
  })

  it('cleans staging and published output when publication cannot finish', () => {
    const fixture_ = fixture()
    try {
      writeArtifact(results(fixture_, 'scripted-full'), 'junit.xml')
      const destination = path.join(
        fixture_.repositoryRoot,
        'output/e2e-captures/20260831-artifact-export',
      )
      mkdirSync(destination, { recursive: true })
      expect(() => exportFixture(fixture_)).toThrow('already exists')
      rmSync(destination, { recursive: true })
      const receiptPath = path.join(fixture_.runRoot, 'receipt.json')
      writeFileSync(receiptPath, 'existing')
      expect(() => exportFixture(fixture_, { receiptPath })).toThrow('receipt destination')
      expect(() => lstatSync(destination)).toThrow()
      expect(
        readdirSync(path.dirname(destination)).some((name) => name.startsWith('.staging-')),
      ).toBe(false)
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('fails closed when a source ancestor is swapped and swapped back before read', () => {
    const fixture_ = fixture()
    const source = results(fixture_, 'scripted-full')
    try {
      writeArtifact(source, 'junit.xml', '<testsuite/>')
      withSwap('source_read', source, ({ replacement, saved }) => {
        expect(() => exportFixture(fixture_)).toThrow('source changed')
        expect(readFileSync(path.join(source, 'junit.xml'), 'utf8')).toBe('<testsuite/>')
        expect(readFileSync(path.join(replacement, 'attacker-marker.txt'), 'utf8')).toBe(
          'must remain untouched',
        )
        expect(existsSync(saved)).toBe(false)
      })
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('fails closed when the destination ancestor is swapped and swapped back before publish', () => {
    const fixture_ = fixture()
    const destinationParent = path.join(fixture_.repositoryRoot, 'output/e2e-captures')
    try {
      writeArtifact(results(fixture_, 'scripted-full'), 'junit.xml', '<testsuite/>')
      mkdirSync(destinationParent, { recursive: true, mode: 0o700 })
      withSwap('destination_publish', destinationParent, ({ replacement, saved }) => {
        expect(() => exportFixture(fixture_)).toThrow('destination is unsafe')
        expect(readFileSync(path.join(replacement, 'attacker-marker.txt'), 'utf8')).toBe(
          'must remain untouched',
        )
        expect(existsSync(saved)).toBe(false)
        expect(readdirSync(destinationParent)).toEqual([])
      })
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('rolls back export when a receipt ancestor is swapped and swapped back before publish', () => {
    const fixture_ = fixture()
    const receiptParent = path.join(fixture_.runRoot, 'receipts')
    const receiptPath = path.join(receiptParent, 'export.json')
    try {
      writeArtifact(results(fixture_, 'scripted-full'), 'junit.xml', '<testsuite/>')
      mkdirSync(receiptParent, { recursive: true, mode: 0o700 })
      withSwap('receipt_publish', receiptParent, ({ replacement, saved }) => {
        expect(() => exportFixture(fixture_, { receiptPath })).toThrow(
          'receipt destination is unsafe',
        )
        expect(readFileSync(path.join(replacement, 'attacker-marker.txt'), 'utf8')).toBe(
          'must remain untouched',
        )
        expect(existsSync(saved)).toBe(false)
        expect(existsSync(receiptPath)).toBe(false)
        expect(readdirSync(path.join(fixture_.repositoryRoot, 'output/e2e-captures'))).toEqual([])
      })
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('uses Asia-Seoul dates and rejects unsafe export slugs', () => {
    const fixture_ = fixture()
    try {
      writeArtifact(results(fixture_, 'scripted-full'), 'junit.xml')
      const receipt = exportFixture(fixture_, {
        environment: { E2E_EXPORT_SLUG: 'safe-slug' },
        now: new Date('2026-08-30T15:30:00.000Z'),
      })
      expect(receipt.export_directory).toBe('output/e2e-captures/20260831-safe-slug')
      expect(() =>
        exportFixture(fixture_, { environment: { E2E_EXPORT_SLUG: '../unsafe' } }),
      ).toThrow('E2E_EXPORT_SLUG')
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('binds final-attempt exports to the full attempt id and absolute paths', () => {
    const fixture_ = fixture()
    const attemptId = 'b'.repeat(64)
    try {
      writeArtifact(results(fixture_, 'scripted-capture'), 'junit.xml')
      writeArtifact(results(fixture_, 'scripted-capture'), 'case/page.png')
      const receipt = exportFixture(fixture_, {
        project: 'scripted-capture',
        environment: {
          E2E_EXPORT_SLUG: `runtime-policy-final-${attemptId}-capture`,
        },
      })
      expect(receipt.attempt_id).toBe(attemptId)
      expect(receipt.export_directory).toContain(attemptId)
      expect(receipt.export_directory_absolute).toBe(
        path.join(realpathSync(fixture_.repositoryRoot), ...receipt.export_directory.split('/')),
      )
      expect(receipt.screenshots_absolute).toEqual(
        receipt.screenshots.map((value) => path.join(receipt.export_directory_absolute, value)),
      )
      expect(receipt.export_tree_sha256).toMatch(/^[0-9a-f]{64}$/)
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })

  it('parses the bounded F2 child slug without colliding with F3 exports', () => {
    const fixture_ = fixture()
    const attemptId = 'c'.repeat(64)
    const token = 'd'.repeat(16)
    try {
      writeArtifact(results(fixture_, 'scripted-full'), 'junit.xml')
      const receipt = exportFixture(fixture_, {
        environment: {
          E2E_EXPORT_SLUG: `runtime-policy-final-${attemptId}-f2-${token}`,
        },
      })
      expect(receipt.attempt_id).toBe(attemptId)
      expect(receipt.export_directory).toContain(`-f2-${token}`)
      expect(path.basename(receipt.export_directory).length).toBeLessThanOrEqual(128)
      expect(receipt.export_directory).not.toContain(`-${attemptId}-scripted`)
      expect(() =>
        exportFixture(fixture_, {
          environment: {
            E2E_EXPORT_SLUG: `runtime-policy-final-${attemptId}-f2-todo21-runtime-policy-e2e-${token}`,
          },
        }),
      ).toThrow('E2E_EXPORT_SLUG must be a bounded lowercase-safe slug.')
    } finally {
      rmSync(fixture_.repositoryRoot, { recursive: true, force: true })
    }
  })
})
