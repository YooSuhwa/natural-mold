import { lazy, Suspense, type ReactNode } from 'react'
import type { ArtifactKind, ArtifactSummary, ArtifactTextContent } from '@/lib/types'
import { ImagePreviewProvider } from './providers/image-preview'
import { MediaPreviewProvider } from './providers/media-preview'
import { PdfPreviewProvider } from './providers/pdf-preview'
import { HtmlPreviewProvider } from './providers/html-preview'
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

const MermaidPreview = lazy(() =>
  import('./providers/mermaid-preview').then((m) => ({ default: m.MermaidPreview })),
)
const MarkdownPreview = lazy(() =>
  import('./providers/markdown-preview').then((m) => ({ default: m.MarkdownPreview })),
)
const JsonDataPreview = lazy(() =>
  import('./providers/json-data-preview').then((m) => ({ default: m.JsonDataPreview })),
)
const StructuredDataPreview = lazy(() =>
  import('./providers/structured-data-preview').then((m) => ({
    default: m.StructuredDataPreview,
  })),
)
const TableDataPreview = lazy(() =>
  import('./providers/table-data-preview').then((m) => ({ default: m.TableDataPreview })),
)
const HwpPreview = lazy(() =>
  import('./providers/hwp-preview').then((m) => ({ default: m.HwpPreview })),
)
const DocxPreview = lazy(() =>
  import('./providers/docx-preview').then((m) => ({ default: m.DocxPreview })),
)
const XlsxPreview = lazy(() =>
  import('./providers/xlsx-preview').then((m) => ({ default: m.XlsxPreview })),
)
const PptxPreview = lazy(() =>
  import('./providers/pptx-preview').then((m) => ({ default: m.PptxPreview })),
)

function PreviewLoadingFallback() {
  return <div className="h-24 animate-pulse rounded-md bg-muted" aria-hidden />
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
  {
    id: 'hwp-hwpx',
    priority: 89,
    requiresText: false,
    extensions: ['hwp', 'hwpx'],
    mimeTypes: ['application/x-hwp', 'application/x-hwpx', 'application/hwp+zip'],
    render: (props) => (
      <Suspense fallback={<PreviewLoadingFallback />}>
        <HwpPreview {...props} />
      </Suspense>
    ),
  },
  {
    id: 'docx',
    priority: 88,
    requiresText: false,
    extensions: ['docx'],
    mimeTypes: ['application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
    render: (props) => (
      <Suspense fallback={<PreviewLoadingFallback />}>
        <DocxPreview {...props} />
      </Suspense>
    ),
  },
  {
    id: 'xlsx',
    priority: 87,
    requiresText: false,
    extensions: ['xlsx', 'xls'],
    mimeTypes: [
      'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'application/vnd.ms-excel',
    ],
    render: (props) => (
      <Suspense fallback={<PreviewLoadingFallback />}>
        <XlsxPreview {...props} />
      </Suspense>
    ),
  },
  {
    id: 'pptx',
    priority: 86,
    requiresText: false,
    extensions: ['pptx'],
    mimeTypes: ['application/vnd.openxmlformats-officedocument.presentationml.presentation'],
    render: (props) => (
      <Suspense fallback={<PreviewLoadingFallback />}>
        <PptxPreview {...props} />
      </Suspense>
    ),
  },
  HtmlPreviewProvider,
  {
    id: 'mermaid',
    priority: 82,
    requiresText: true,
    extensions: ['mmd', 'mermaid'],
    match: (artifact) => ['mmd', 'mermaid'].includes(artifact.extension ?? ''),
    render: (props) => (
      <Suspense fallback={<PreviewLoadingFallback />}>
        <MermaidPreview {...props} />
      </Suspense>
    ),
  },
  {
    id: 'markdown',
    priority: 80,
    requiresText: true,
    kinds: ['markdown'],
    extensions: ['md', 'markdown'],
    mimeTypes: ['text/markdown'],
    match: (artifact) => artifact.artifact_kind === 'markdown',
    render: (props) => (
      <Suspense fallback={<PreviewLoadingFallback />}>
        <MarkdownPreview {...props} />
      </Suspense>
    ),
  },
  {
    id: 'table-data',
    priority: 78,
    requiresText: true,
    extensions: ['csv', 'tsv'],
    mimeTypes: ['text/csv', 'text/tab-separated-values'],
    render: (props) => (
      <Suspense fallback={<PreviewLoadingFallback />}>
        <TableDataPreview {...props} />
      </Suspense>
    ),
  },
  {
    id: 'json-data',
    priority: 79,
    requiresText: true,
    extensions: ['json'],
    mimeTypes: ['application/json'],
    render: (props) => (
      <Suspense fallback={<PreviewLoadingFallback />}>
        <JsonDataPreview {...props} />
      </Suspense>
    ),
  },
  {
    id: 'structured-data',
    priority: 78,
    requiresText: true,
    extensions: ['yaml', 'yml', 'toml'],
    mimeTypes: [
      'application/yaml',
      'application/x-yaml',
      'text/yaml',
      'application/toml',
      'text/toml',
    ],
    render: (props) => (
      <Suspense fallback={<PreviewLoadingFallback />}>
        <StructuredDataPreview {...props} />
      </Suspense>
    ),
  },
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
