import { describe, expect, it } from 'vitest'

import {
  addResourceContextReference,
  parseResourceContextReferences,
  removeResourceContextReference,
  mergeResourceContextMetadata,
  resourceContextMetadata,
} from '../resource-context'

const fileRef = {
  kind: 'file',
  id: '8f65c7bb-7092-43f4-a215-8e603101d114',
  label: 'notes.txt',
} as const

describe('resource context contract', () => {
  it('serializes public provenance under the authoritative run input key', () => {
    expect(resourceContextMetadata([fileRef])).toEqual({ resource_context: [fileRef] })
    expect(mergeResourceContextMetadata({ trace_id: 'draft-1' }, [fileRef])).toEqual({
      trace_id: 'draft-1',
      resource_context: [fileRef],
    })
    expect(
      mergeResourceContextMetadata({ trace_id: 'draft-1', resource_context: [fileRef] }, []),
    ).toEqual({ trace_id: 'draft-1' })
  })

  it('rejects browser paths, snapshot text, and versions on non-artifacts', () => {
    expect(parseResourceContextReferences([]).success).toBe(false)
    expect(
      parseResourceContextReferences([
        { ...fileRef, path: '/Users/private.txt', snapshot: 'secret' },
      ]).success,
    ).toBe(false)
    expect(
      parseResourceContextReferences([{ ...fileRef, version_id: crypto.randomUUID() }]).success,
    ).toBe(false)
  })

  it('caps refs at eight and preserves exact artifact versions', () => {
    const refs = Array.from({ length: 8 }, (_, index) => ({
      kind: 'artifact' as const,
      id: crypto.randomUUID(),
      version_id: crypto.randomUUID(),
      label: `Artifact ${index}`,
    }))
    const ninth = { kind: 'skill' as const, id: crypto.randomUUID(), label: 'Skill' }

    expect(addResourceContextReference(refs, ninth)).toEqual({ kind: 'limit', refs })
    expect(parseResourceContextReferences(refs)).toEqual({ success: true, data: refs })
  })

  it('deduplicates and removes provenance without changing the remaining order', () => {
    const artifact = {
      kind: 'artifact' as const,
      id: crypto.randomUUID(),
      version_id: crypto.randomUUID(),
      label: 'Report',
    }
    const once = addResourceContextReference([fileRef], artifact)
    expect(once.kind).toBe('added')
    if (once.kind !== 'added') return

    expect(addResourceContextReference(once.refs, artifact)).toEqual({
      kind: 'duplicate',
      refs: once.refs,
    })
    expect(removeResourceContextReference(once.refs, fileRef)).toEqual([artifact])
  })

  it('rejects duplicate public references before submission', () => {
    expect(parseResourceContextReferences([fileRef, fileRef]).success).toBe(false)
  })
})
