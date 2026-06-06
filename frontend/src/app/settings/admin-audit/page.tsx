'use client'

import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useTranslations } from 'next-intl'

import { SettingsShell } from '../_components/settings-shell'
import { AuditEventsContent } from '../audit/_components/audit-events-content'
import { useSession } from '@/lib/auth/session'

export default function AdminAuditSettingsPage() {
  const router = useRouter()
  const t = useTranslations('appSettings.audit')
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
      <AuditEventsContent scope="all" admin />
    </SettingsShell>
  )
}
