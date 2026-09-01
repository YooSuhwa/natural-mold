/** Finite, secret-free phase marker for one flaky E2E scenario. */

export const FAILURE_PHASE_ANNOTATION_TYPE = 'moldy.failure-phase.v1'

export const FAILURE_PHASES = [
  'setup_agent',
  'open_draft',
  'verify_draft_route',
  'install_prompt_observer',
  'submit_prompt',
  'wait_draft_promotion',
  'wait_prompt',
  'wait_ask_user_card',
  'verify_prompt_stability',
  'select_option',
  'submit_decision',
  'wait_final_response',
  'verify_final_prompt',
  'verify_error_collectors',
  'cleanup_parent_agent',
  'cleanup_child_agent',
  'complete',
] as const

export type FailurePhase = (typeof FAILURE_PHASES)[number]

type PlaywrightAnnotation = {
  type: string
  description?: string
}

/** Replace the reserved marker while preserving unrelated Playwright annotations. */
export function setFailurePhase(annotations: PlaywrightAnnotation[], phase: FailurePhase): void {
  const unrelated = annotations.filter(({ type }) => type !== FAILURE_PHASE_ANNOTATION_TYPE)
  annotations.splice(0, annotations.length, ...unrelated, {
    type: FAILURE_PHASE_ANNOTATION_TYPE,
    description: phase,
  })
}
