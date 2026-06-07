'use client'

import { useRouter } from 'next/navigation'
import { useLocale, useTranslations } from 'next-intl'
import { useTheme } from 'next-themes'
import { CheckIcon, GlobeIcon, MonitorIcon, MoonIcon, PaletteIcon, SunIcon } from 'lucide-react'

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { isSupportedLocale, SUPPORTED_LOCALES } from '@/i18n/locales'
import { persistLocaleCookie } from '@/i18n/client-locale'
import { cn } from '@/lib/utils'

export function AppearanceSettingsContent() {
  const t = useTranslations('appSettings.appearance')

  return (
    <div className="space-y-4">
      <section className="space-y-1">
        <h2 className="text-lg font-semibold text-foreground">{t('title')}</h2>
        <p className="text-sm leading-6 text-muted-foreground">{t('description')}</p>
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        <ThemeCard />
        <LanguageCard />
      </div>
    </div>
  )
}

function ThemeCard() {
  const { theme, setTheme } = useTheme()
  const t = useTranslations('appSettings.appearance.theme')

  const themes = [
    { value: 'light', label: t('light'), icon: SunIcon },
    { value: 'dark', label: t('dark'), icon: MoonIcon },
    { value: 'system', label: t('system'), icon: MonitorIcon },
  ] as const

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <PaletteIcon className="size-4" aria-hidden />
          {t('title')}
        </CardTitle>
        <CardDescription>{t('description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {themes.map(({ value, label, icon: Icon }) => {
          const active = theme === value
          return (
            <button
              key={value}
              type="button"
              aria-pressed={active}
              onClick={() => setTheme(value)}
              className={cn(
                'flex h-10 w-full items-center justify-between rounded-lg border px-3 text-sm font-medium transition-[background-color,border-color,color]',
                active
                  ? 'border-primary bg-primary/10 text-primary-strong'
                  : 'border-border bg-background text-foreground hover:bg-muted',
              )}
            >
              <span className="flex items-center gap-2">
                <Icon className="size-4" aria-hidden />
                {label}
              </span>
              {active ? <CheckIcon className="size-4" aria-hidden /> : null}
            </button>
          )
        })}
      </CardContent>
    </Card>
  )
}

function LanguageCard() {
  const router = useRouter()
  const locale = useLocale()
  const t = useTranslations('appSettings.appearance.language')
  const currentLocale = isSupportedLocale(locale) ? locale : SUPPORTED_LOCALES[0]

  function changeLocale(nextLocale: string) {
    if (!isSupportedLocale(nextLocale) || nextLocale === currentLocale) return
    persistLocaleCookie(nextLocale)
    router.refresh()
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <GlobeIcon className="size-4" aria-hidden />
          {t('title')}
        </CardTitle>
        <CardDescription>{t('description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {SUPPORTED_LOCALES.map((option) => {
          const active = option === currentLocale
          return (
            <button
              key={option}
              type="button"
              aria-pressed={active}
              onClick={() => changeLocale(option)}
              className={cn(
                'flex h-10 w-full items-center justify-between rounded-lg border px-3 text-sm font-medium transition-[background-color,border-color,color]',
                active
                  ? 'border-primary bg-primary/10 text-primary-strong'
                  : 'border-border bg-background text-foreground hover:bg-muted',
              )}
            >
              <span>{t(option)}</span>
              {active ? <CheckIcon className="size-4" aria-hidden /> : null}
            </button>
          )
        })}
      </CardContent>
    </Card>
  )
}
