'use client'

import { useActiveOrganization } from '@/lib/hooks/use-organizations'
import {
  useCreateGrant,
  useResourceAccess,
  useRevokeGrant,
  useTransferOwnership,
} from '@/lib/hooks/use-resource-sharing'
import type {
  AccessEntry,
  DelegatedAccount,
  AccessLevel,
  AccessSource,
  GrantPrincipalType,
  OwnershipTransfer,
  ShareLevel,
  ShareResourceType,
} from '@/lib/types/grant'
import type { PrincipalSearchType } from '@/lib/types/organization'

/** Every surface the dialog serves: the five E-01 types plus marketplace items (I-04 adapter). */
export type ShareSurface = ShareResourceType | 'marketplace_item'

/** A recipient picked in the dialog. */
export interface ShareSubject {
  type: GrantPrincipalType
  id: string
  displayName: string
}

export interface ShareEntry {
  key: string
  /** Revoke handle (`grant_id` or the marketplace ACL user id). */
  revokeId: string | null
  principal: {
    type: GrantPrincipalType
    id: string
    displayName: string | null
    memberCount: number | null
  }
  /** `null` when the backend does not report a level (marketplace ACL rows). */
  level: AccessLevel | null
  source: AccessSource
  revocable: boolean
  expiresAt: string | null
}

export interface ShareSnapshot {
  entries: ShareEntry[]
  canShare: boolean
  canRevoke: boolean
  canGovern: boolean
  /** Accounts an agent lends (D17), confirmed before sharing; empty for other types. */
  delegations?: DelegatedAccount[]
}

export interface ShareRequest {
  principal: ShareSubject
  level: string
  expiresAt: string | null
  confirmDelegation: boolean
}

/**
 * What the dialog needs from a sharing API. `useResourceShareBackend` speaks the
 * E-01 grants API; marketplace items keep their ACL endpoints until I-04.
 */
export interface ShareBackend {
  surface: ShareSurface
  snapshot: ShareSnapshot | undefined
  isLoading: boolean
  error: unknown
  refetch: () => void
  /** `false` → list and revoke only (marketplace ACL until I-04). */
  allowsAdd: boolean
  levels: readonly string[]
  principalTypes: readonly PrincipalSearchType[]
  /** Only agent run (`consumer`) shares may expire (10.2). */
  allowsExpiry: (level: string) => boolean
  share: (request: ShareRequest) => Promise<void>
  revoke: (entry: ShareEntry) => Promise<void>
  /** `null` when the surface has no ownership transfer. */
  transfer: ((body: OwnershipTransfer) => Promise<void>) | null
  pending: boolean
}

const FULL_LEVELS: readonly ShareLevel[] = ['consumer', 'viewer', 'editor', 'manager']
const CREDENTIAL_LEVELS: readonly ShareLevel[] = ['consumer', 'manager']

/** 10.1 levels per resource type (backend `share_targets.SHARE_TYPES`). */
export function shareLevelsFor(type: ShareResourceType): readonly ShareLevel[] {
  return type === 'credential' ? CREDENTIAL_LEVELS : FULL_LEVELS
}

function isShareLevel(value: string): value is ShareLevel {
  return (FULL_LEVELS as readonly string[]).includes(value)
}

function toEntry(entry: AccessEntry): ShareEntry {
  const { principal } = entry
  return {
    key: `${entry.source}:${principal.type}:${principal.id}:${entry.grant_id ?? ''}`,
    revokeId: entry.grant_id,
    principal: {
      type: principal.type,
      id: principal.id,
      displayName: principal.display_name,
      memberCount: principal.member_count,
    },
    level: entry.level,
    source: entry.source,
    revocable: entry.revocable && entry.grant_id !== null,
    expiresAt: entry.expires_at,
  }
}

export function useResourceShareBackend(
  type: ShareResourceType,
  resourceId: string,
  enabled = true,
): ShareBackend {
  const access = useResourceAccess(type, resourceId, enabled)
  const create = useCreateGrant(type, resourceId)
  const revoke = useRevokeGrant(type, resourceId)
  const transfer = useTransferOwnership(type, resourceId)
  const { activeOrganization } = useActiveOrganization()
  const orgWide = activeOrganization?.capabilities.includes('cap_share_org_wide') ?? false
  const permissions = access.data?.my_permissions ?? []

  return {
    surface: type,
    snapshot: access.data
      ? {
          entries: access.data.items.map(toEntry),
          canShare: permissions.includes('can_share'),
          canRevoke: permissions.includes('can_revoke'),
          canGovern: permissions.includes('can_govern'),
          delegations: access.data.delegations ?? [],
        }
      : undefined,
    isLoading: access.isLoading,
    error: access.error,
    refetch: () => void access.refetch(),
    allowsAdd: true,
    levels: shareLevelsFor(type),
    principalTypes: orgWide ? ['user', 'group', 'org'] : ['user', 'group'],
    allowsExpiry: (level) => type === 'agent' && level === 'consumer',
    share: async ({ principal, level, expiresAt, confirmDelegation }) => {
      if (!isShareLevel(level)) throw new Error(`unsupported share level: ${level}`)
      await create.mutateAsync({
        principal: { type: principal.type, id: principal.id },
        level,
        expires_at: expiresAt,
        confirm_delegation: confirmDelegation,
      })
    },
    revoke: async (entry) => {
      if (entry.revokeId) await revoke.mutateAsync(entry.revokeId)
    },
    transfer: async (body) => {
      await transfer.mutateAsync(body)
    },
    pending: create.isPending || revoke.isPending || transfer.isPending,
  }
}
