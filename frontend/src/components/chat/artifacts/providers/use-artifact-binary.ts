import { useQuery } from '@tanstack/react-query'
import { artifactKeys } from '@/lib/api/artifacts'
import { resolveImageUrl } from '@/lib/utils'
import type { ArtifactSummary } from '@/lib/types'

export function useArtifactArrayBuffer(artifact: ArtifactSummary) {
  return useQuery({
    queryKey: artifactKeys.binary(artifact.id, artifact.version_id),
    queryFn: async () => {
      const url = resolveImageUrl(artifact.preview_url) ?? artifact.preview_url
      const response = await fetch(url, { credentials: 'include' })
      if (!response.ok) throw new Error(`Failed to fetch artifact: ${response.status}`)
      return response.arrayBuffer()
    },
    staleTime: 30_000,
  })
}
