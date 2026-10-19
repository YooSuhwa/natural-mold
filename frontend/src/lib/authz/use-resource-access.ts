'use client'

import { useCallback, useMemo } from 'react'

import type { PermissionResourceType } from '@/lib/api/resource-permissions'
import { useResourcePermissions } from '@/lib/hooks/use-resource-permissions'

import type { PermissionSource } from './permissions'
import { usePermissionCheck } from './use-permission'

/** A list/detail row of a type whose bodies carry no `my_permissions` (H-05). */
export interface AccessRow {
  id: string
  /** Absent on bodies without an owner field (skills); `null` = platform row (tools). */
  user_id?: string | null
  is_system?: boolean
}

const READ_ONLY: ReadonlySet<string> = new Set(['can_use', 'can_view'])

function sourceOf(row: AccessRow, myPermissions: readonly string[] | undefined): PermissionSource {
  const hasOwner = 'user_id' in row
  return {
    myPermissions,
    // Without an answer (loading, 503, row left out) fail closed: only a known owner
    // keeps edit actions; a body without an owner field is treated as someone else's.
    ownerId: hasOwner ? row.user_id : null,
    isSystem: row.is_system ?? (hasOwner && row.user_id === null),
  }
}

/**
 * Per-row gate for tools, MCP servers, credentials and skills: the server's
 * `my_permissions` once answered; until then, and when the answer failed (503), rows
 * the caller does not own are read-only (plan 13.9, fail closed).
 */
export function useResourceAccess(
  type: PermissionResourceType,
  rows: readonly AccessRow[] | null | undefined,
) {
  const ids = useMemo(() => (rows ?? []).map((row) => row.id), [rows])
  const { permissionsOf, error, refetch } = useResourcePermissions(type, ids)
  const check = usePermissionCheck()
  const can = useCallback(
    (row: AccessRow | null | undefined, permission: string): boolean => {
      if (!row) return false
      const answer = permissionsOf(row.id)
      // A listed row is at least usable; only changing it waits for the answer.
      if (!answer && READ_ONLY.has(permission)) return true
      return check(sourceOf(row, answer), permission)
    },
    [check, permissionsOf],
  )
  return { can, permissionsOf, error, refetch }
}
