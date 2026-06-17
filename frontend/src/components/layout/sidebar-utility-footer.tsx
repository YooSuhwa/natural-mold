'use client'

import { CheckIcon, Globe2Icon, MoonIcon, SunIcon } from 'lucide-react'
import { useLocale, useTranslations } from 'next-intl'
import { useTheme } from 'next-themes'
import { useRouter } from 'next/navigation'
import { useState } from 'react'

import { UserMenu } from '@/components/auth/UserMenu'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  SidebarFooter,
  SidebarMenu,
  SidebarMenuItem,
  SidebarSeparator,
} from '@/components/ui/sidebar'
import { Skeleton } from '@/components/ui/skeleton'
import { useSession } from '@/lib/auth/session'
import { useLogout } from '@/lib/hooks/useAuth'
import { persistLocaleCookie } from '../../i18n/client-locale'
import { SUPPORTED_LOCALES, isSupportedLocale } from '../../i18n/locales'

export function SidebarUtilityFooter() {
  const router = useRouter()
  const { resolvedTheme, setTheme } = useTheme()
  const locale = useLocale()
  const t = useTranslations('sidebar')
  const { data: user } = useSession()
  const logout = useLogout()
  const [languageOpen, setLanguageOpen] = useState(false)
  const isDarkTheme = resolvedTheme === 'dark'
  const themeToggleLabel = isDarkTheme ? t('theme.light') : t('theme.dark')
  const currentLocale = isSupportedLocale(locale) ? locale : SUPPORTED_LOCALES[0]
  const languageLabel = t('language.label')

  function changeLocale(nextLocale: string) {
    setLanguageOpen(false)
    if (!isSupportedLocale(nextLocale) || nextLocale === currentLocale) return
    persistLocaleCookie(nextLocale)
    router.refresh()
  }

  return (
    <SidebarFooter className="shrink-0 bg-transparent px-3 pb-3">
      <SidebarSeparator />
      <div className="flex items-center justify-center gap-1 px-2 py-1 group-data-[collapsible=icon]:hidden">
        <button
          type="button"
          onClick={() => setTheme(isDarkTheme ? 'light' : 'dark')}
          suppressHydrationWarning
          className="moldy-sidebar-control inline-flex h-7 items-center justify-center rounded-lg px-1.5"
          aria-label={themeToggleLabel}
          title={themeToggleLabel}
        >
          <span className="relative inline-flex size-4 items-center justify-center">
            <SunIcon className="absolute size-4 opacity-0 dark:opacity-100" />
            <MoonIcon className="absolute size-4 opacity-100 dark:opacity-0" />
          </span>
        </button>
        <DropdownMenu open={languageOpen} onOpenChange={setLanguageOpen}>
          <DropdownMenuTrigger
            render={<button type="button" />}
            className="moldy-sidebar-control inline-flex h-7 items-center gap-1 rounded-lg px-1.5 text-xs font-semibold data-open:bg-sidebar-accent data-open:text-sidebar-accent-foreground"
            aria-label={languageLabel}
            title={languageLabel}
          >
            <Globe2Icon className="size-4" />
            <span>{currentLocale.toUpperCase()}</span>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="top" align="center" className="w-36">
            {SUPPORTED_LOCALES.map((option) => (
              <DropdownMenuItem
                key={option}
                onClick={() => changeLocale(option)}
                className="justify-between"
              >
                <span>{t(`language.${option}`)}</span>
                {option === currentLocale ? (
                  <CheckIcon className="size-4 text-primary-strong" />
                ) : (
                  <span className="size-4" />
                )}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <SidebarMenu>
        <SidebarMenuItem>
          {user ? (
            <UserMenu user={user} onLogout={() => logout.mutate()} />
          ) : (
            <div className="flex items-center gap-3 px-2 py-2 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:gap-0 group-data-[collapsible=icon]:px-0">
              <Skeleton className="size-8 rounded-lg" />
              <div className="flex-1 space-y-1 group-data-[collapsible=icon]:hidden">
                <Skeleton className="h-3 w-24" />
                <Skeleton className="h-3 w-32" />
              </div>
            </div>
          )}
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarFooter>
  )
}
