'use client'

import { useAui, useAuiState } from '@assistant-ui/react'
import { useCallback, useEffect, useState } from 'react'

import {
  addResourceContextReference,
  mergeResourceContextMetadata,
  removeResourceContextReference,
  resourceContextMetadata,
  type AddResourceContextResult,
  type ResourceContextReference,
} from './resource-context'

const EMPTY_CUSTOM: Readonly<Record<string, unknown>> = {}
const EMPTY_REFERENCES: readonly ResourceContextReference[] = []

export function useResourceContextComposer(
  resetKey?: string | number | null,
  initialReferences: readonly ResourceContextReference[] = EMPTY_REFERENCES,
): {
  readonly references: readonly ResourceContextReference[]
  readonly add: (reference: ResourceContextReference) => AddResourceContextResult
  readonly remove: (reference: ResourceContextReference) => void
  readonly clear: () => void
  readonly update: (reference: ResourceContextReference, next: ResourceContextReference) => void
} {
  const aui = useAui()
  const [selection, setSelection] = useState<{
    readonly resetKey: typeof resetKey
    readonly refs: readonly ResourceContextReference[]
  }>(() => ({ resetKey, refs: initialReferences }))
  const references = Object.is(selection.resetKey, resetKey) ? selection.refs : EMPTY_REFERENCES
  const runConfigCustom = useAuiState((state) => state.composer.runConfig.custom ?? EMPTY_CUSTOM)
  // Zod rebuilds field order (and trims text). Compare the same canonical shape
  // written to runConfig, otherwise equivalent refs can trigger an update loop.
  const selectedKey = JSON.stringify(
    references.length ? resourceContextMetadata(references).resource_context : [],
  )
  const configuredKey = JSON.stringify(runConfigCustom.resource_context ?? null)

  useEffect(() => {
    if (references.length === 0 || selectedKey === configuredKey) return
    aui.composer.setRunConfig({
      custom: mergeResourceContextMetadata(runConfigCustom, references),
    })
  }, [aui, configuredKey, references, runConfigCustom, selectedKey])

  const add = useCallback(
    (reference: ResourceContextReference): AddResourceContextResult => {
      const result = addResourceContextReference(references, reference)
      if (result.kind === 'added') setSelection({ resetKey, refs: result.refs })
      return result
    },
    [references, resetKey],
  )
  const remove = useCallback(
    (reference: ResourceContextReference): void => {
      setSelection({ resetKey, refs: removeResourceContextReference(references, reference) })
    },
    [references, resetKey],
  )
  const clear = useCallback((): void => setSelection({ resetKey, refs: [] }), [resetKey])
  const update = useCallback(
    (reference: ResourceContextReference, next: ResourceContextReference) => {
      setSelection({ resetKey, refs: references.map((item) => (item === reference ? next : item)) })
    },
    [references, resetKey],
  )

  useEffect(() => {
    if (references.length > 0) return
    const nextCustom = mergeResourceContextMetadata(runConfigCustom, [])
    if (nextCustom === runConfigCustom || !Object.hasOwn(runConfigCustom, 'resource_context'))
      return
    aui.composer.setRunConfig({ custom: nextCustom })
  }, [aui, references.length, runConfigCustom])

  return { references, add, remove, clear, update }
}
