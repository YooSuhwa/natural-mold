import type { ArtifactSummary } from '@/lib/types'

type MessageMetadataWithArtifacts = {
  custom?: {
    artifacts?: ArtifactSummary[] | null
  }
}

const EMPTY_MESSAGE_ARTIFACTS: ArtifactSummary[] = []

export function selectMessageArtifactsFromMetadata(metadata: unknown): ArtifactSummary[] {
  const artifacts = (metadata as MessageMetadataWithArtifacts | undefined)?.custom?.artifacts
  return Array.isArray(artifacts) ? artifacts : EMPTY_MESSAGE_ARTIFACTS
}
