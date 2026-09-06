import { z } from 'zod'

export const MAX_RESOURCE_CONTEXT_REFERENCES = 8

const baseReferenceShape = {
  id: z.string().uuid(),
  label: z.string().trim().min(1).max(255).optional(),
}

export const resourceContextReferenceSchema = z.discriminatedUnion('kind', [
  z
    .object({ kind: z.literal('file'), ...baseReferenceShape })
    .strict()
    .readonly(),
  z
    .object({
      kind: z.literal('artifact'),
      ...baseReferenceShape,
      version_id: z.string().uuid().optional(),
    })
    .strict()
    .readonly(),
  z
    .object({ kind: z.literal('skill'), ...baseReferenceShape })
    .strict()
    .readonly(),
  z
    .object({ kind: z.literal('conversation'), ...baseReferenceShape })
    .strict()
    .readonly(),
])

function rejectDuplicateReferences(
  references: readonly z.infer<typeof resourceContextReferenceSchema>[],
  context: z.RefinementCtx,
): void {
  const identities = new Set<string>()
  references.forEach((reference, index) => {
    const version = reference.kind === 'artifact' ? (reference.version_id ?? '') : ''
    const identity = `${reference.kind}:${reference.id}:${version}`
    if (identities.has(identity)) {
      context.addIssue({ code: 'custom', message: 'Duplicate resource reference', path: [index] })
    }
    identities.add(identity)
  })
}

export const resourceContextReferencesSchema = z
  .array(resourceContextReferenceSchema)
  .min(1)
  .max(MAX_RESOURCE_CONTEXT_REFERENCES)
  .superRefine(rejectDuplicateReferences)
  .readonly()

export const resourceContextResponseReferencesSchema = z
  .array(resourceContextReferenceSchema)
  .max(MAX_RESOURCE_CONTEXT_REFERENCES)
  .superRefine(rejectDuplicateReferences)
  .readonly()

export type ResourceContextReference = z.infer<typeof resourceContextReferenceSchema>
export type ResourceContextKind = ResourceContextReference['kind']

export type ResourceContextCandidate = ResourceContextReference & {
  readonly description?: string
}

export type AddResourceContextResult =
  | { readonly kind: 'added'; readonly refs: readonly ResourceContextReference[] }
  | { readonly kind: 'duplicate'; readonly refs: readonly ResourceContextReference[] }
  | { readonly kind: 'limit'; readonly refs: readonly ResourceContextReference[] }

function versionId(reference: ResourceContextReference): string {
  return reference.kind === 'artifact' ? (reference.version_id ?? '') : ''
}

export function resourceContextReferenceKey(reference: ResourceContextReference): string {
  return `${reference.kind}:${reference.id}:${versionId(reference)}`
}

export function parseResourceContextReferences(value: unknown) {
  return resourceContextReferencesSchema.safeParse(value)
}

export function addResourceContextReference(
  refs: readonly ResourceContextReference[],
  reference: ResourceContextReference,
): AddResourceContextResult {
  const key = resourceContextReferenceKey(reference)
  if (refs.some((current) => resourceContextReferenceKey(current) === key)) {
    return { kind: 'duplicate', refs }
  }
  if (refs.length >= MAX_RESOURCE_CONTEXT_REFERENCES) return { kind: 'limit', refs }
  return { kind: 'added', refs: [...refs, reference] }
}

export function removeResourceContextReference(
  refs: readonly ResourceContextReference[],
  reference: ResourceContextReference,
): readonly ResourceContextReference[] {
  const key = resourceContextReferenceKey(reference)
  return refs.filter((current) => resourceContextReferenceKey(current) !== key)
}

export function resourceContextMetadata(refs: readonly ResourceContextReference[]): {
  readonly resource_context: readonly ResourceContextReference[]
} {
  const parsed = resourceContextReferencesSchema.parse(refs)
  return { resource_context: parsed }
}

export function mergeResourceContextMetadata(
  existing: Readonly<Record<string, unknown>>,
  refs: readonly ResourceContextReference[],
): Readonly<Record<string, unknown>> {
  if (refs.length === 0) {
    const { resource_context: _removed, ...remaining } = existing
    void _removed
    return remaining
  }
  return { ...existing, ...resourceContextMetadata(refs) }
}
