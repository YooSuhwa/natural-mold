import type {
  ExitAction,
  ExitResolution,
  ExitResourceType,
  MemberExitPreview,
  OwnedResource,
} from '@/lib/types/member-exit'

const EXIT_TYPES: readonly ExitResourceType[] = [
  'agent',
  'skill',
  'tool',
  'mcp_server',
  'credential',
]

/** `blocked_by` keys that block a transfer (E-01 `transfer_blocked` details). */
const TRANSFER_BLOCKER_KEYS = ['triggers', 'deployments', 'api_keys', 'credentials'] as const
export type BlockerKind = (typeof TRANSFER_BLOCKER_KEYS)[number] | 'skill_bindings' | 'other'

export interface Blocker {
  kind: BlockerKind
  count: number
}

export type ActionableResource = OwnedResource & { resource_type: ExitResourceType }

/** A resource the wizard settles (transfer or delete) — or cannot settle. */
export type RemovalRow =
  | { kind: 'archived'; resource: OwnedResource }
  | { kind: 'unsupported'; resource: OwnedResource }
  | { kind: 'blocked'; resource: OwnedResource; blockers: Blocker[] }
  | {
      kind: 'actionable'
      resource: ActionableResource
      canTransfer: boolean
      canDelete: boolean
    }

export interface RowChoice {
  action: ExitAction
  newOwner: { id: string; displayName: string } | null
}

function isExitType(value: string): value is ExitResourceType {
  return (EXIT_TYPES as readonly string[]).includes(value)
}

function count(value: unknown): number {
  return Array.isArray(value) ? value.length : 1
}

function blockerKind(key: string): BlockerKind {
  if (key === 'skill_bindings') return 'skill_bindings'
  return (TRANSFER_BLOCKER_KEYS as readonly string[]).includes(key) ? (key as BlockerKind) : 'other'
}

/**
 * Classify one owned resource (10.7 "구성원 제거"). Personal-account credentials can
 * only be deleted (10.4); skill bindings block only that delete; dependents a transfer
 * cannot carry (another user's trigger, deployment, key) block the resource entirely.
 */
export function classify(resource: OwnedResource): RemovalRow {
  if (resource.archived) return { kind: 'archived', resource }
  if (!isExitType(resource.resource_type)) return { kind: 'unsupported', resource }
  const details = resource.blocked_by ?? {}
  const transferKeys = Object.keys(details).filter(
    (key) => key !== 'personal_account' && key !== 'skill_bindings',
  )
  const canTransfer = resource.transferable && transferKeys.length === 0
  const canDelete = resource.resource_type === 'credential' && !('skill_bindings' in details)
  if (!canTransfer && !canDelete) {
    const blockers = Object.keys(details)
      .filter((key) => key !== 'personal_account')
      .map((key) => ({ kind: blockerKind(key), count: count(details[key]) }))
    return { kind: 'blocked', resource, blockers }
  }
  return {
    kind: 'actionable',
    resource: { ...resource, resource_type: resource.resource_type },
    canTransfer,
    canDelete,
  }
}

export function rowKey(resource: OwnedResource): string {
  return `${resource.resource_type}:${resource.resource_id}`
}

export function defaultChoice(row: RemovalRow): RowChoice {
  const transfer = row.kind === 'actionable' && row.canTransfer
  return { action: transfer ? 'transfer' : 'delete', newOwner: null }
}

export interface RemovalPlan {
  rows: RemovalRow[]
  /** Anything the wizard cannot settle: the removal stays closed (fail closed). */
  blocked: boolean
}

export function planRemoval(preview: MemberExitPreview): RemovalPlan {
  const rows = preview.owned.map(classify)
  const blocked =
    preview.is_last_admin ||
    rows.some((row) => row.kind === 'blocked' || row.kind === 'unsupported')
  return { rows, blocked }
}

/** Resolutions for `POST …/removal`, or `null` while a row still lacks a decision. */
export function buildResolutions(
  plan: RemovalPlan,
  choices: ReadonlyMap<string, RowChoice>,
): ExitResolution[] | null {
  if (plan.blocked) return null
  const resolutions: ExitResolution[] = []
  for (const row of plan.rows) {
    if (row.kind !== 'actionable') continue
    const choice = choices.get(rowKey(row.resource)) ?? defaultChoice(row)
    const { resource_type, resource_id } = row.resource
    if (choice.action === 'delete') {
      if (!row.canDelete) return null
      resolutions.push({ resource_type, resource_id, action: 'delete' })
      continue
    }
    if (!row.canTransfer || !choice.newOwner) return null
    resolutions.push({
      resource_type,
      resource_id,
      action: 'transfer',
      new_owner_id: choice.newOwner.id,
    })
  }
  return resolutions
}
