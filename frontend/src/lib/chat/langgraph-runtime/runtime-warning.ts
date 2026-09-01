import { reportClientWarning } from '@/lib/logging/client-logger'

export type RuntimeFailureCode =
  | 'initial_hydration_failed'
  | 'branch_hydration_failed'
  | 'edit_hydration_failed'
  | 'reload_hydration_failed'
  | 'post_run_hydration_failed'
  | 'cancel_active_lookup_failed'
  | 'hitl_flush_failed'

export function reportRuntimeFailure(_caught: unknown, code: RuntimeFailureCode): void {
  reportClientWarning('useMoldyLangGraphStream', `Runtime operation failed: ${code}`)
}
