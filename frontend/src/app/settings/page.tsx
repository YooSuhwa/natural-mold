'use client'

import { SunIcon, MoonIcon, MonitorIcon, UserIcon, PaletteIcon, GlobeIcon } from 'lucide-react'
import { useTheme } from 'next-themes'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { PageHeader } from '@/components/shared/page-header'
import { cn } from '@/lib/utils'

export default function SettingsPage() {
  const t = useTranslations('appSettings')

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader title={t('title')} />

      <div className="mx-auto w-full max-w-2xl space-y-6">
        {/* Profile Card */}
        <ProfileCard />

        {/* Theme + Language (2-column) */}
        <div className="grid gap-4 sm:grid-cols-2">
          <ThemeCard />
          <LanguageCard />
        </div>
      </div>
    </div>
  )
}

function ProfileCard() {
  const t = useTranslations('appSettings.profile')

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <UserIcon className="size-4" />
          {t('title')}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex items-center justify-between py-1">
          <span className="text-sm text-muted-foreground">{t('name')}</span>
          <span className="text-sm font-medium">{t('mockName')}</span>
        </div>
        <div className="border-t border-foreground/5" />
        <div className="flex items-center justify-between py-1">
          <span className="text-sm text-muted-foreground">{t('email')}</span>
          <span className="text-sm font-medium">{t('mockEmail')}</span>
        </div>
      </CardContent>
    </Card>
  )
}

function ThemeCard() {
  const { theme, setTheme } = useTheme()
  const t = useTranslations('appSettings.theme')

  const themes = [
    { value: 'light', label: t('light'), icon: SunIcon },
    { value: 'dark', label: t('dark'), icon: MoonIcon },
    { value: 'system', label: t('system'), icon: MonitorIcon },
  ] as const

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <PaletteIcon className="size-4" />
          {t('title')}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {themes.map(({ value, label, icon: Icon }) => (
          <button
            key={value}
            type="button"
            onClick={() => setTheme(value)}
            className={cn(
              'flex w-full items-center gap-3 rounded-lg border p-3',
              'cursor-pointer hover:bg-accent transition-colors duration-200',
              theme === value && 'border-primary bg-primary/5',
            )}
          >
            <Icon className="size-4" />
            <span className="text-sm font-medium">{label}</span>
          </button>
        ))}
      </CardContent>
    </Card>
  )
}

function LanguageCard() {
  const t = useTranslations('appSettings.language')
  const tc = useTranslations('common')

  const languages = [
    { value: 'ko', label: t('ko') },
    { value: 'en', label: t('en') },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <GlobeIcon className="size-4" />
          {t('title')}
          <span className="text-xs font-normal text-muted-foreground">
            ({tc('comingSoon.default')})
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {languages.map(({ value, label }) => (
          <button
            key={value}
            type="button"
            onClick={() => toast.info(tc('comingSoon.default'))}
            className={cn(
              'flex w-full items-center gap-3 rounded-lg border p-3',
              'transition-colors duration-200',
              value === 'ko'
                ? 'border-primary bg-primary/5'
                : 'opacity-50 cursor-pointer hover:opacity-70',
            )}
          >
            <span className="text-sm font-medium">{label}</span>
          </button>
        ))}
      </CardContent>
    </Card>
  )
}
