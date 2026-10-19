'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'

import { useRegisterUnsavedOrgDraft } from '@/lib/org-scope/unsaved-drafts'

/** Unsaved-draft guards of the settings screen; returns the header's back action. */
export function useSettingsLeaveGuard(agentId: string, isDirty: boolean): () => void {
  const router = useRouter()
  const t = useTranslations('agent.settings')

  useRegisterUnsavedOrgDraft(isDirty)

  useEffect(() => {
    if (!isDirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [isDirty])

  return function handleBack() {
    if (isDirty && !window.confirm(t('unsavedWarning'))) return
    const hasAppHistory =
      typeof document !== 'undefined' &&
      document.referrer &&
      new URL(document.referrer).origin === window.location.origin
    if (hasAppHistory) {
      router.back()
    } else {
      router.push(`/agents/${agentId}`)
    }
  }
}
