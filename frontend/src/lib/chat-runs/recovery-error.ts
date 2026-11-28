const recoveryErrorCodes = [
  'recovery_effect_unconfirmed',
  'recovery_steer_unconfirmed',
  'recovery_checkpoint_unavailable',
  'recovery_policy_changed',
  'recovery_limit_reached',
  'recovery_model_attempt_limit',
  'recovery_model_attempt_unproven',
  'recovery_output_started',
  'recovery_authority_revoked',
  'recovery_launch_failed',
  'recovery_disabled',
  'recovery_tool_not_allowed',
  'tool_result_unavailable',
  'tool_receipt_conflict',
  'tool_call_in_progress',
] as const

export type RecoveryErrorCode = (typeof recoveryErrorCodes)[number]

export function recoveryErrorCode(value: unknown): RecoveryErrorCode | undefined {
  return recoveryErrorCodes.find((code) => code === value)
}

/** Require review before replaying input with prior effects, output, or an exhausted send budget. */
export function blocksInputRetry(code: RecoveryErrorCode | undefined): boolean {
  return (
    code === 'recovery_effect_unconfirmed' ||
    code === 'recovery_steer_unconfirmed' ||
    code === 'recovery_output_started' ||
    code === 'recovery_model_attempt_limit' ||
    code === 'recovery_model_attempt_unproven'
  )
}
