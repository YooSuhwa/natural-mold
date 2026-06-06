import { getExternalStoreMessages } from '@assistant-ui/react'
import type { ArtifactSummary } from '@/lib/types'

type MessageMetadataWithArtifacts = {
  custom?: {
    artifacts?: ArtifactSummary[] | null
  }
}

type MessageWithMetadata = {
  metadata?: unknown
}

type ExternalMessageWithArtifacts = {
  artifacts?: ArtifactSummary[] | null
}

const EMPTY_MESSAGE_ARTIFACTS: ArtifactSummary[] = []

export function selectMessageArtifactsFromMetadata(metadata: unknown): ArtifactSummary[] {
  const artifacts = (metadata as MessageMetadataWithArtifacts | undefined)?.custom?.artifacts
  return Array.isArray(artifacts) ? artifacts : EMPTY_MESSAGE_ARTIFACTS
}

export function selectMessageArtifactsFromMessage(message: unknown): ArtifactSummary[] {
  const metadataArtifacts = selectMessageArtifactsFromMetadata(
    (message as MessageWithMetadata | undefined)?.metadata,
  )
  if (metadataArtifacts.length > 0) return metadataArtifacts
  if (!message || typeof message !== 'object') return EMPTY_MESSAGE_ARTIFACTS

  const externalMessages = getExternalStoreMessages<ExternalMessageWithArtifacts>(
    message as Parameters<typeof getExternalStoreMessages>[0],
  )
  for (const externalMessage of externalMessages) {
    if (Array.isArray(externalMessage.artifacts) && externalMessage.artifacts.length > 0) {
      return externalMessage.artifacts
    }
  }
  return EMPTY_MESSAGE_ARTIFACTS
}
