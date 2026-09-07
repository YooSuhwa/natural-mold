import type { JsonValue } from '@/lib/api/conversation-run-inputs'

import { parseResourceContextReferences, type ResourceContextReference } from './resource-context'

const SERVER_SNAPSHOT_KEY = '_moldy_resource_context_v1'

export type ResourceContextPayloadResult =
  | {
      readonly kind: 'valid'
      readonly input: Readonly<Record<string, JsonValue>>
      readonly refs: readonly ResourceContextReference[]
    }
  | { readonly kind: 'invalid'; readonly reason: 'malformed' | 'reserved-server-field' }

export function withResourceContextReferences(
  input: Readonly<Record<string, JsonValue>>,
  references: unknown,
): ResourceContextPayloadResult {
  if (SERVER_SNAPSHOT_KEY in input) return { kind: 'invalid', reason: 'reserved-server-field' }
  const parsed = parseResourceContextReferences(references)
  if (!parsed.success) return { kind: 'invalid', reason: 'malformed' }
  return {
    kind: 'valid',
    input: { ...input, resource_context: parsed.data.map((reference) => ({ ...reference })) },
    refs: parsed.data,
  }
}

export function withResourceContextRunInput(
  input: Readonly<Record<string, JsonValue>>,
  metadata: Readonly<Record<string, unknown>> | undefined,
): ResourceContextPayloadResult {
  if (SERVER_SNAPSHOT_KEY in input) return { kind: 'invalid', reason: 'reserved-server-field' }
  if (!metadata || !Object.hasOwn(metadata, 'resource_context')) {
    return { kind: 'valid', input, refs: [] }
  }
  return withResourceContextReferences(input, metadata.resource_context)
}
