import { useQuery } from '@tanstack/react-query'
import { artifactKeys, getArtifactArrayBuffer } from '@/lib/api/artifacts'
import { resolveImageUrl } from '@/lib/utils'
import type { ArtifactSummary } from '@/lib/types'

export function useArtifactArrayBuffer(artifact: ArtifactSummary) {
  return useQuery({
    queryKey: artifactKeys.binary(artifact.id, artifact.version_id),
    queryFn: async () => {
      const url = resolveImageUrl(artifact.preview_url) ?? artifact.preview_url
      return getArtifactArrayBuffer(url)
    },
    staleTime: 30_000,
  })
}
