import type { ResourceContextCandidate } from './resource-context'

type FileCandidateSource = {
  readonly source: 'generated' | 'attached'
  readonly id: string
  readonly name: string
  readonly mime_type: string
}

type ArtifactCandidateSource = {
  readonly status: 'writing' | 'ready' | 'deleted' | 'failed'
  readonly id: string
  readonly version_id: string
  readonly display_name: string
  readonly mime_type: string
}

type SkillCandidateSource = {
  readonly id: string
  readonly name: string
  readonly description?: string | null
}

type ConversationCandidateSource = {
  readonly id: string
  readonly title: string | null
}

export function buildResourceContextCandidates({
  files,
  artifacts,
  skills,
  conversations,
}: {
  readonly files: readonly FileCandidateSource[]
  readonly artifacts: readonly ArtifactCandidateSource[]
  readonly skills: readonly SkillCandidateSource[]
  readonly conversations: readonly ConversationCandidateSource[]
}): readonly ResourceContextCandidate[] {
  const attachedFiles: ResourceContextCandidate[] = files
    .filter((file) => file.source === 'attached')
    .map((file) => ({ kind: 'file', id: file.id, label: file.name, description: file.mime_type }))
  const artifactRefs: ResourceContextCandidate[] = artifacts
    .filter((artifact) => artifact.status === 'ready')
    .map((artifact) => ({
      kind: 'artifact',
      id: artifact.id,
      version_id: artifact.version_id,
      label: artifact.display_name,
      description: artifact.mime_type,
    }))
  const skillRefs: ResourceContextCandidate[] = skills.map((skill) => ({
    kind: 'skill',
    id: skill.id,
    label: skill.name,
    ...(skill.description ? { description: skill.description } : {}),
  }))
  const conversationRefs: ResourceContextCandidate[] = conversations.map((conversation) => ({
    kind: 'conversation',
    id: conversation.id,
    label: conversation.title?.trim() || conversation.id,
  }))

  return [...attachedFiles, ...artifactRefs, ...skillRefs, ...conversationRefs]
}
