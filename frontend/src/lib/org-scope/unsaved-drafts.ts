'use client'

import { useEffect, useId } from 'react'

/**
 * Registry of screens holding unsaved edits, consulted before an organization
 * switch discards them (plan 13.11: "저장하지 않은 초안은 확인 후 버림").
 */
const dirtyDrafts = new Set<string>()

export function hasUnsavedOrgDrafts(): boolean {
  return dirtyDrafts.size > 0
}

export function useRegisterUnsavedOrgDraft(isDirty: boolean): void {
  const id = useId()
  useEffect(() => {
    if (!isDirty) return
    dirtyDrafts.add(id)
    return () => {
      dirtyDrafts.delete(id)
    }
  }, [id, isDirty])
}
