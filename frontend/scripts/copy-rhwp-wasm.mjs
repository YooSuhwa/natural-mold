import { copyFile, mkdir } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..')
const source = join(root, 'node_modules', '@rhwp', 'core', 'rhwp_bg.wasm')
const targetDir = join(root, 'public', 'vendor', 'rhwp')
const target = join(targetDir, 'rhwp_bg.wasm')

await mkdir(targetDir, { recursive: true })
await copyFile(source, target)
console.log(`copied ${source} -> ${target}`)
