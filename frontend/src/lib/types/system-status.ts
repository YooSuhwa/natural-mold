// Operator system status — mirrors backend `app/schemas/system_status.py` (app/install).

export const INSTALL_PROFILES = [
  'minimal',
  'external_api',
  'gpu',
  'kubernetes',
  'airgapped',
] as const
export type InstallProfile = (typeof INSTALL_PROFILES)[number]

export const SYSTEM_COMPONENTS = [
  'database',
  'pgvector',
  'worker',
  'object_store',
  'mcp_runtime',
  'pii_detector',
  'skill_sandbox',
] as const
export type SystemComponent = (typeof SYSTEM_COMPONENTS)[number]

export type ComponentRequirement = 'required' | 'recommended' | 'optional'
export type ComponentFound = 'ok' | 'off' | 'error'
export type SystemVerdict = 'ok' | 'warning' | 'error'

export interface ComponentStatusOut {
  key: SystemComponent
  requirement: ComponentRequirement
  found: ComponentFound
  verdict: SystemVerdict
  /** Technical detail from the check (English, shown as-is). */
  detail: string
}

export interface WorkerOut {
  worker_id: string
  hostname: string
  job_types: string[]
  started_at: string
  heartbeat_at: string
  live: boolean
}

export interface QueueOut {
  queued: number
  running: number
  overdue: number
  failed_last_day: number
}

export interface SystemStatusOut {
  profile: InstallProfile
  verdict: SystemVerdict
  components: ComponentStatusOut[]
  workers: WorkerOut[]
  queue: QueueOut
  checked_at: string
}
