import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'

import { ROOT_MESSAGE_NAMESPACES } from '@/i18n/scoped-messages'

// A route layout hands its subtree only the namespaces it lists (plus the root set).
// A component that asks for another one renders raw keys, and component tests do not
// notice because they mount with every message. This walks what each route imports.

const SRC = resolve(__dirname, '../../../src')
const APP = join(SRC, 'app')
const EXTENSIONS = ['.tsx', '.ts', '/index.tsx', '/index.ts']

function sourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) {
      return name === '__tests__' ? [] : sourceFiles(path)
    }
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) ? [path] : []
  })
}

function resolveImport(from: string, specifier: string): string | null {
  const base = specifier.startsWith('@/')
    ? join(SRC, specifier.slice(2))
    : specifier.startsWith('.')
      ? resolve(dirname(from), specifier)
      : null
  if (!base) return null
  for (const extension of EXTENSIONS) {
    if (existsSync(base + extension)) return base + extension
  }
  return null
}

const cache = new Map<string, { namespaces: string[]; imports: string[] }>()

function read(file: string) {
  const known = cache.get(file)
  if (known) return known
  const text = readFileSync(file, 'utf8')
  const namespaces = [
    ...text.matchAll(/(?:useTranslations|getTranslations)\(\s*['"]([A-Za-z0-9_]+)/g),
  ].map((match) => match[1])
  const imports = [...text.matchAll(/(?:from|import)\s*\(?\s*['"]([^'"]+)['"]/g)]
    .map((match) => resolveImport(file, match[1]))
    .filter((path): path is string => path !== null)
  const entry = { namespaces, imports }
  cache.set(file, entry)
  return entry
}

function layoutNamespaces(layout: string): string[] | null {
  const text = readFileSync(layout, 'utf8')
  if (!text.includes('ScopedIntlProvider')) return null
  const list = text.match(/_NAMESPACES\s*=\s*\[([\s\S]*?)\]\s*as const/)
  return list
    ? [...list[1].replace(/\/\/.*$/gm, '').matchAll(/['"]([A-Za-z0-9_]+)['"]/g)].map((m) => m[1])
    : []
}

function scopeOf(file: string): Set<string> | null {
  // Only a route under a scoped layout is checked; the nearest providers stack.
  const scope = new Set<string>(ROOT_MESSAGE_NAMESPACES)
  let scoped = false
  for (let dir = dirname(file); dir.startsWith(APP) && dir !== APP; dir = dirname(dir)) {
    const layout = join(dir, 'layout.tsx')
    const listed = existsSync(layout) ? layoutNamespaces(layout) : null
    if (listed) {
      scoped = true
      listed.forEach((namespace) => scope.add(namespace))
    }
  }
  return scoped ? scope : null
}

function missing(): string[] {
  const found = new Set<string>()
  for (const file of sourceFiles(APP)) {
    const scope = scopeOf(file)
    if (!scope) continue
    const route = file.slice(APP.length + 1).split('/')[0]
    // The route's own file and what it imports directly: deeper imports reach shared
    // surfaces (chat, dialogs) that many routes pull in without ever rendering them.
    const direct = read(file).imports.filter((path) => !path.startsWith(APP))
    for (const current of [file, ...direct]) {
      for (const namespace of read(current).namespaces) {
        if (!scope.has(namespace)) {
          found.add(`${route}: ${namespace} (${current.slice(SRC.length + 1)})`)
        }
      }
    }
  }
  return [...found].sort()
}

describe('route message scope', () => {
  it('gives every route the namespaces its components ask for', () => {
    expect(missing()).toEqual([])
  })
})
