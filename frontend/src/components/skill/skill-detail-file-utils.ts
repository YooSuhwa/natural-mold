const TEXT_EXTENSIONS = new Set([
  'md',
  'markdown',
  'txt',
  'py',
  'js',
  'jsx',
  'ts',
  'tsx',
  'json',
  'yaml',
  'yml',
  'toml',
  'css',
  'html',
  'sh',
  'bash',
  'zsh',
  'sql',
  'env',
  'gitignore',
  'rst',
  'log',
])

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'])

function getExt(path: string): string {
  const index = path.lastIndexOf('.')
  if (index === -1) return ''
  return path.slice(index + 1).toLowerCase()
}

export function isTextFile(path: string): boolean {
  const ext = getExt(path)
  if (!ext) return true
  return TEXT_EXTENSIONS.has(ext)
}

export function isImageFile(path: string): boolean {
  return IMAGE_EXTENSIONS.has(getExt(path))
}

export function isPdf(path: string): boolean {
  return getExt(path) === 'pdf'
}
