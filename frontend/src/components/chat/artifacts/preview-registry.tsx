import type { ReactNode } from 'react'
import type { ArtifactKind, ArtifactSummary, ArtifactTextContent } from '@/lib/types'
import { ImagePreviewProvider } from './providers/image-preview'
import { MediaPreviewProvider } from './providers/media-preview'
import { PdfPreviewProvider } from './providers/pdf-preview'
import { HtmlPreviewProvider } from './providers/html-preview'
import { MermaidPreviewProvider } from './providers/mermaid-preview'
import { MarkdownPreviewProvider } from './providers/markdown-preview'
import { JsonDataPreviewProvider } from './providers/json-data-preview'
import { StructuredDataPreviewProvider } from './providers/structured-data-preview'
import { TableDataPreviewProvider } from './providers/table-data-preview'
import { CodePreviewProvider } from './providers/code-preview'
import { TextPreviewProvider } from './providers/text-preview'
import { FallbackPreviewProvider } from './providers/fallback-preview'

export interface ArtifactPreviewProps {
  artifact: ArtifactSummary
  textContent?: ArtifactTextContent | null
  isLoadingText?: boolean
}

export interface ArtifactPreviewProvider {
  id: string
  priority: number
  requiresText: boolean
  kinds?: ArtifactKind[]
  extensions?: string[]
  mimeTypes?: string[]
  match?: (artifact: ArtifactSummary) => boolean
  render: (props: ArtifactPreviewProps) => ReactNode
}

const providerById = new Map<string, ArtifactPreviewProvider>()

export const artifactPreviewProviders: ArtifactPreviewProvider[] = []

function normalizeExtension(extension: string | null | undefined): string | null {
  return extension?.replace(/^\./, '').toLowerCase() || null
}

function normalizeMimeType(mimeType: string | null | undefined): string {
  return (mimeType ?? '').toLowerCase()
}

function mimeTypeMatches(pattern: string, mimeType: string): boolean {
  const normalizedPattern = pattern.toLowerCase()
  if (normalizedPattern.endsWith('/*')) {
    return mimeType.startsWith(normalizedPattern.slice(0, -1))
  }
  return normalizedPattern === mimeType
}

function providerMatchesArtifact(
  provider: ArtifactPreviewProvider,
  artifact: ArtifactSummary,
): boolean {
  const extension = normalizeExtension(artifact.extension)
  const mimeType = normalizeMimeType(artifact.mime_type)
  if (provider.kinds?.includes(artifact.artifact_kind)) return true
  if (extension && provider.extensions?.map(normalizeExtension).includes(extension)) return true
  if (provider.mimeTypes?.some((pattern) => mimeTypeMatches(pattern, mimeType))) return true
  return provider.match?.(artifact) ?? false
}

export function registerArtifactPreviewProvider(provider: ArtifactPreviewProvider): void {
  providerById.set(provider.id, provider)
  artifactPreviewProviders.splice(
    0,
    artifactPreviewProviders.length,
    ...Array.from(providerById.values()).sort((left, right) => right.priority - left.priority),
  )
}

export function registerArtifactPreviewProviders(providers: ArtifactPreviewProvider[]): void {
  for (const provider of providers) registerArtifactPreviewProvider(provider)
}

registerArtifactPreviewProviders([
  ImagePreviewProvider,
  MediaPreviewProvider,
  PdfPreviewProvider,
  HtmlPreviewProvider,
  MermaidPreviewProvider,
  MarkdownPreviewProvider,
  TableDataPreviewProvider,
  JsonDataPreviewProvider,
  StructuredDataPreviewProvider,
  CodePreviewProvider,
  TextPreviewProvider,
  FallbackPreviewProvider,
])

export function getArtifactPreviewProvider(artifact: ArtifactSummary): ArtifactPreviewProvider {
  return (
    artifactPreviewProviders.find((provider) => providerMatchesArtifact(provider, artifact)) ??
    FallbackPreviewProvider
  )
}
