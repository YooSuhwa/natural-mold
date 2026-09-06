'use client'

import { useMemo } from 'react'

import { useArtifactLibrary } from '@/lib/hooks/use-artifact-library'
import { useConversationFiles } from '@/lib/hooks/use-conversation-files'
import { useGlobalConversationPages } from '@/lib/hooks/use-conversations'
import type { SkillBrief } from '@/lib/types'

import { buildResourceContextCandidates } from './resource-context-candidates'
import type { ResourceContextCandidate } from './resource-context'

const EMPTY_SKILLS: readonly SkillBrief[] = []

export function useResourceContextCandidates({
  conversationId,
  linkedSkills = EMPTY_SKILLS,
}: {
  readonly conversationId: string | null | undefined
  readonly linkedSkills?: readonly SkillBrief[]
}): {
  readonly candidates: readonly ResourceContextCandidate[]
  readonly isLoading: boolean
  readonly hasError: boolean
} {
  const files = useConversationFiles(conversationId)
  const artifacts = useArtifactLibrary({ limit: 100 })
  const conversations = useGlobalConversationPages({ limit: 100 })
  const artifactRows = useMemo(
    () => artifacts.data?.pages.flatMap((page) => page.items) ?? [],
    [artifacts.data?.pages],
  )
  const conversationRows = useMemo(
    () => conversations.data?.pages.flatMap((page) => page.items) ?? [],
    [conversations.data?.pages],
  )
  const candidates = useMemo(
    () =>
      buildResourceContextCandidates({
        files: files.data ?? [],
        artifacts: artifactRows,
        skills: linkedSkills,
        conversations: conversationRows,
      }),
    [artifactRows, conversationRows, files.data, linkedSkills],
  )

  return {
    candidates,
    isLoading: files.isLoading || artifacts.isLoading || conversations.isLoading,
    hasError: files.isError || artifacts.isError || conversations.isError,
  }
}
