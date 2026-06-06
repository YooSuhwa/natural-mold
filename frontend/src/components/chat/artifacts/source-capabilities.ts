import type { ArtifactSummary } from '@/lib/types'

const TEXT_SOURCE_EXTENSIONS = new Set([
  'csv',
  'tsv',
  'json',
  'txt',
  'log',
  'yaml',
  'yml',
  'toml',
  'md',
  'markdown',
  'mmd',
  'mermaid',
  'html',
  'htm',
  'py',
  'js',
  'ts',
  'tsx',
  'jsx',
  'css',
  'sql',
  'sh',
])

export function canShowArtifactSource(artifact: ArtifactSummary): boolean {
  const extension = artifact.extension?.replace(/^\./, '').toLowerCase() ?? ''
  const mimeType = artifact.mime_type.toLowerCase()
  if (mimeType.startsWith('text/')) return true
  if (artifact.artifact_kind === 'markdown' || artifact.artifact_kind === 'html') return true
  if (artifact.artifact_kind === 'code') return true
  return TEXT_SOURCE_EXTENSIONS.has(extension)
}
