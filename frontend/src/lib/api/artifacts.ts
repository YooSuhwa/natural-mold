import { apiFetch } from './client'
import type {
  ArtifactLibraryPage,
  ArtifactLibraryParams,
  ArtifactLibraryStats,
  ArtifactSummary,
  ArtifactTextContent,
} from '@/lib/types'

function appendParam(params: URLSearchParams, key: string, value: unknown): void {
  if (value === undefined || value === null || value === '') return
  params.set(key, String(value))
}

function withQuery(path: string, values: Record<string, unknown>): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) appendParam(params, key, value)
  const query = params.toString()
  return query ? `${path}?${query}` : path
}

export const artifactKeys = {
  all: ['artifacts'] as const,
  conversation: (conversationId: string | null | undefined) =>
    ['artifacts', 'conversation', conversationId ?? 'none'] as const,
  content: (artifactId: string | null | undefined, versionId: string | null | undefined) =>
    ['artifacts', 'content', artifactId ?? 'none', versionId ?? 'none'] as const,
  binary: (artifactId: string, versionId: string | null | undefined) =>
    ['artifact-binary', artifactId, versionId] as const,
  library: (params: ArtifactLibraryParams) => ['artifacts', 'library', params] as const,
  stats: ['artifacts', 'stats'] as const,
  recent: (limit: number) => ['artifacts', 'recent', limit] as const,
}

export function listConversationArtifacts(conversationId: string): Promise<ArtifactSummary[]> {
  return apiFetch<ArtifactSummary[]>(`/api/conversations/${conversationId}/artifacts`)
}

export function getArtifactTextContent(artifactId: string): Promise<ArtifactTextContent> {
  return apiFetch<ArtifactTextContent>(`/api/artifacts/${artifactId}/content`)
}

export async function getArtifactArrayBuffer(url: string): Promise<ArrayBuffer> {
  const response = await fetch(url, { credentials: 'include' })
  if (!response.ok) throw new Error(`Failed to fetch artifact: ${response.status}`)
  return response.arrayBuffer()
}

export function listArtifactLibrary(
  params: ArtifactLibraryParams = {},
): Promise<ArtifactLibraryPage> {
  return apiFetch<ArtifactLibraryPage>(
    withQuery('/api/artifacts', {
      q: params.q,
      agent_id: params.agent_id,
      conversation_id: params.conversation_id,
      kind: params.kind,
      favorite: params.favorite,
      limit: params.limit,
      cursor: params.cursor,
    }),
  )
}

export function getArtifactLibraryStats(): Promise<ArtifactLibraryStats> {
  return apiFetch<ArtifactLibraryStats>('/api/artifacts/stats')
}

export function listRecentArtifacts(limit = 20): Promise<ArtifactSummary[]> {
  return apiFetch<ArtifactSummary[]>(withQuery('/api/artifacts/recent', { limit }))
}

export function setArtifactFavorite(
  artifactId: string,
  isFavorite: boolean,
): Promise<ArtifactSummary> {
  return apiFetch<ArtifactSummary>(`/api/artifacts/${artifactId}`, {
    method: 'PATCH',
    body: JSON.stringify({ is_favorite: isFavorite }),
  })
}

export function recordArtifactOpened(artifactId: string): Promise<ArtifactSummary> {
  return apiFetch<ArtifactSummary>(`/api/artifacts/${artifactId}/opened`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}
