export type RunActivityKind =
  | 'thinking'
  | 'planning'
  | 'tool'
  | 'subagent'
  | 'background_subagent'
  | 'artifact'
  | 'memory'
  | 'interrupt'
  | 'checkpoint'
  | 'responding'
  | 'reconnecting'
  | 'done'
  | 'error'

export type RunActivityStatus =
  | 'pending'
  | 'running'
  | 'requires_action'
  | 'complete'
  | 'error'
  | 'cancelled'

export interface RunActivity {
  readonly id: string
  readonly runId: string
  readonly kind: RunActivityKind
  readonly status: RunActivityStatus
  readonly title: string
  readonly subtitle?: string
  readonly namespace: readonly string[]
  readonly startedAt?: string
  readonly endedAt?: string
  readonly toolCallId?: string
  readonly parentId?: string
  readonly data?: Record<string, unknown>
}

export interface ProtocolEvent {
  readonly type?: string
  readonly method: string
  readonly params?: {
    readonly namespace?: readonly string[]
    readonly data?: unknown
    readonly timestamp?: string
  }
  readonly seq?: number
  readonly event_id?: string
  readonly run_id?: string
}
