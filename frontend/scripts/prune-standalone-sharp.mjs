import { existsSync, lstatSync, readdirSync, rmSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const projectRoot = dirname(dirname(fileURLToPath(import.meta.url)))
const standaloneRoot = join(projectRoot, '.next', 'standalone')

const shouldPrune = (name) =>
  name === 'sharp' ||
  name === 'colour' ||
  name.startsWith('sharp@') ||
  name.startsWith('@img+sharp') ||
  name.startsWith('@img+colour') ||
  name.startsWith('sharp-')

let removed = 0

function walk(dir) {
  if (!existsSync(dir)) return

  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const target = join(dir, entry.name)

    if (shouldPrune(entry.name)) {
      rmSync(target, { recursive: true, force: true })
      removed += 1
      continue
    }

    if (!entry.isDirectory()) continue

    try {
      if (lstatSync(target).isSymbolicLink()) continue
    } catch {
      continue
    }

    walk(target)
  }
}

walk(standaloneRoot)

if (removed > 0) {
  console.log(`Pruned ${removed} sharp-related entr${removed === 1 ? 'y' : 'ies'} from .next/standalone.`)
}
