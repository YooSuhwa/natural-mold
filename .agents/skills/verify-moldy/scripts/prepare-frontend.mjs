// Copy source into the run so Next can rewrite tsconfig/next-env and prepare assets
// without changing the developer's frontend. Dependencies remain read-only symlinks.
import fs from 'node:fs'
import path from 'node:path'

const [repo, target] = process.argv.slice(2)
if (!repo || !target) throw new Error('usage: prepare-frontend.mjs <repo> <target>')
const source = path.join(repo, 'frontend')
fs.mkdirSync(target, { recursive: true })
for (const name of ['src', 'messages', 'public', 'scripts', 'package.json', 'tsconfig.json', 'postcss.config.mjs']) {
  fs.cpSync(path.join(source, name), path.join(target, name), { recursive: true })
}
fs.symlinkSync(path.join(source, 'node_modules'), path.join(target, 'node_modules'), 'dir')
const configImport = path.relative(target, path.join(source, 'next.config.ts')).split(path.sep).join('/')
fs.writeFileSync(path.join(target, 'next.config.ts'),
  `import config from ${JSON.stringify(configImport)}\nexport default config\n`)
console.log(`frontend snapshot: ${target}`)
