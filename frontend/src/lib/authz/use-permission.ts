'use client'

import { useCallback } from 'react'

import { useSession } from '@/lib/auth/session'

import { hasPermission, type PermissionSource } from './permissions'

/** `hasPermission` bound to the signed-in user (plan 13.1 `usePermission`). */
export function usePermissionCheck(): (source: PermissionSource, permission: string) => boolean {
  const { data: user } = useSession()
  const userId = user?.id
  return useCallback(
    (source: PermissionSource, permission: string) => hasPermission(source, permission, userId),
    [userId],
  )
}

export function usePermission(source: PermissionSource, permission: string): boolean {
  return usePermissionCheck()(source, permission)
}
