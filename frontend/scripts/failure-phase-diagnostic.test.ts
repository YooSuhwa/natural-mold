import { describe, expect, it } from 'vitest'

import {
  FAILURE_PHASE_ANNOTATION_TYPE,
  FAILURE_PHASES,
  setFailurePhase,
  type FailurePhase,
} from '../e2e/helpers/failure-phase-diagnostic'

describe('failure phase diagnostics', () => {
  it.each(FAILURE_PHASES)('replaces the reserved annotation with the exact %s phase', (phase) => {
    const annotations = [
      { type: 'unrelated-first', description: 'keep-first' },
      { type: FAILURE_PHASE_ANNOTATION_TYPE, description: 'setup_agent' },
      { type: 'unrelated-last', description: 'keep-last' },
      { type: FAILURE_PHASE_ANNOTATION_TYPE, description: 'complete' },
    ]

    setFailurePhase(annotations, phase)

    expect(annotations).toEqual([
      { type: 'unrelated-first', description: 'keep-first' },
      { type: 'unrelated-last', description: 'keep-last' },
      { type: FAILURE_PHASE_ANNOTATION_TYPE, description: phase },
    ])
    expect(Object.keys(annotations[2] ?? {})).toEqual(['type', 'description'])
  })

  it('keeps all finite phase values representable', () => {
    const annotations: { type: string; description?: string }[] = []

    for (const phase of FAILURE_PHASES as readonly FailurePhase[]) {
      setFailurePhase(annotations, phase)
      expect(annotations.at(-1)?.description).toBe(phase)
    }
  })
})
