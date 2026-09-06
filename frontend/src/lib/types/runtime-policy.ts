/**
 * Portable, versioned runtime metadata owned by an agent.
 *
 * This intentionally excludes resolved provenance and conversation snapshots:
 * those values belong to the server-side execution boundary and must never be
 * copied through agent settings, marketplace, or deployment payloads.
 */
export interface RuntimePolicyFilesystemV1 {
  mode: 'inspect' | 'artifact_write'
}

export interface RuntimePolicyTodoV1 {
  enabled: boolean
}

export type RuntimePolicySummarizationV1 =
  | { mode: 'auto' }
  | { mode: 'preset'; preset: 'balanced_context_v1' }

export interface RuntimePolicyV1 {
  version: 1
  filesystem: RuntimePolicyFilesystemV1
  todo: RuntimePolicyTodoV1
  summarization: RuntimePolicySummarizationV1
}
