'use client'

import type { ReactNode } from 'react'
import { useFormatter, useTranslations } from 'next-intl'
import { CalendarDaysIcon, ClockIcon, MailIcon, ShieldIcon, UserIcon } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useSession } from '@/lib/auth/session'
import { SettingsShell } from './_components/settings-shell'

export default function SettingsPage() {
  const t = useTranslations('appSettings.profile')
  const format = useFormatter()
  const { data: user, isPending } = useSession()

  function formatDate(value?: string | null) {
    if (!value) return t('never')
    return format.dateTime(new Date(value), {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    })
  }

  return (
    <SettingsShell>
      <div className="space-y-4">
        <section className="space-y-1">
          <h2 className="text-lg font-semibold text-foreground">{t('title')}</h2>
          <p className="text-sm leading-6 text-muted-foreground">{t('description')}</p>
        </section>

        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="space-y-1">
                <CardTitle className="flex items-center gap-2">
                  <UserIcon className="size-4" aria-hidden />
                  {t('accountInfo')}
                </CardTitle>
                <CardDescription>{t('accountInfoDescription')}</CardDescription>
              </div>
              {user?.is_super_user ? (
                <Badge variant="secondary" className="bg-status-accent/15 text-status-accent">
                  <ShieldIcon className="size-3" aria-hidden />
                  {t('adminBadge')}
                </Badge>
              ) : null}
            </div>
          </CardHeader>
          <CardContent>
            {isPending || !user ? (
              <p className="text-sm text-muted-foreground">{t('loading')}</p>
            ) : (
              <dl className="grid gap-3 sm:grid-cols-2">
                <ProfileField icon={<UserIcon className="size-4" />} label={t('name')}>
                  {user.name}
                </ProfileField>
                <ProfileField icon={<MailIcon className="size-4" />} label={t('email')}>
                  {user.email}
                </ProfileField>
                <ProfileField
                  icon={<CalendarDaysIcon className="size-4" />}
                  label={t('joinedAt')}
                >
                  {formatDate(user.created_at)}
                </ProfileField>
                <ProfileField icon={<ClockIcon className="size-4" />} label={t('lastLoginAt')}>
                  {formatDate(user.last_login_at)}
                </ProfileField>
              </dl>
            )}
          </CardContent>
        </Card>
      </div>
    </SettingsShell>
  )
}

function ProfileField({
  icon,
  label,
  children,
}: {
  icon: ReactNode
  label: string
  children: ReactNode
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-muted/30 p-3">
      <dt className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <span className="text-primary-strong" aria-hidden>
          {icon}
        </span>
        {label}
      </dt>
      <dd className="mt-2 break-words text-sm font-medium text-foreground">{children}</dd>
    </div>
  )
}
