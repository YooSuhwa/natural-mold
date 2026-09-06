import type { Unstable_MentionCategory } from '@assistant-ui/react'
import { z } from 'zod'

import {
  resourceContextReferenceKey,
  type ResourceContextCandidate,
  type ResourceContextKind,
  type ResourceContextReference,
} from './resource-context'

const triggerMetadataSchema = z
  .object({
    resource_kind: z.enum(['file', 'artifact', 'skill', 'conversation']),
    resource_id: z.string().uuid(),
    resource_version_id: z.string().uuid().optional(),
    resource_label: z.string().trim().min(1).max(255).optional(),
  })
  .strict()

const KIND_ORDER = ['file', 'artifact', 'skill', 'conversation'] as const

export function createResourceMentionCategories(
  candidates: readonly ResourceContextCandidate[],
  labels: Readonly<Record<ResourceContextKind, string>>,
): readonly Unstable_MentionCategory[] {
  return KIND_ORDER.map((kind) => ({
    id: kind,
    label: labels[kind],
    items: candidates
      .filter((candidate) => candidate.kind === kind)
      .map((candidate) => ({
        id: resourceContextReferenceKey(candidate),
        type: 'resource-context',
        label: candidate.label ?? candidate.id,
        ...(candidate.description ? { description: candidate.description } : {}),
        metadata: {
          resource_kind: candidate.kind,
          resource_id: candidate.id,
          ...(candidate.kind === 'artifact' && candidate.version_id
            ? { resource_version_id: candidate.version_id }
            : {}),
          ...(candidate.label ? { resource_label: candidate.label } : {}),
        },
      })),
  })).filter((category) => category.items.length > 0)
}

export function resourceReferenceFromTriggerMetadata(
  metadata: unknown,
): ResourceContextReference | null {
  const result = triggerMetadataSchema.safeParse(metadata)
  if (!result.success) return null
  const value = result.data
  if (value.resource_kind !== 'artifact' && value.resource_version_id) return null
  const common = {
    id: value.resource_id,
    ...(value.resource_label ? { label: value.resource_label } : {}),
  }
  switch (value.resource_kind) {
    case 'file':
      return { kind: 'file', ...common }
    case 'artifact':
      return {
        kind: 'artifact',
        ...common,
        ...(value.resource_version_id ? { version_id: value.resource_version_id } : {}),
      }
    case 'skill':
      return { kind: 'skill', ...common }
    case 'conversation':
      return { kind: 'conversation', ...common }
  }
}
