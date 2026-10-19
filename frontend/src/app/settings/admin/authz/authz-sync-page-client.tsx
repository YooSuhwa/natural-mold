'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'

import { SettingsShell } from '../../_components/settings-shell'
import { AuthzSyncContent } from '@/features/platform-authz/components/authz-sync-content'
import { useSession } from '@/lib/auth/session'

/**
 * S27 권한 동기화 상태 (plan 14.3; H-04). The backend gates every endpoint with
 * `require_super_user`; this guard only hides the chrome for other users.
 */
export function AuthzSyncPageClient() {
  const router = useRouter()
  const t = useTranslations('appSettings.authzOps')
  const { data: user, isPending } = useSession()
  const denied = !isPending && !!user && !user.is_super_user

  useEffect(() => {
    if (denied) router.replace('/')
  }, [denied, router])

  if (isPending || denied) {
    return (
      <SettingsShell>
        <p className="text-sm text-muted-foreground">{t('loading')}</p>
      </SettingsShell>
    )
  }

  return (
    <SettingsShell>
      <AuthzSyncContent />
    </SettingsShell>
  )
}
