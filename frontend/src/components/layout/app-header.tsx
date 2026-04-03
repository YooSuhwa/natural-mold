'use client'

import { useTranslations } from 'next-intl'
import { SidebarTrigger } from '@/components/ui/sidebar'
import { Separator } from '@/components/ui/separator'

export function AppHeader() {
  const t = useTranslations('sidebar')

  return (
    <header className="flex h-12 shrink-0 items-center gap-2 border-b px-4">
      <SidebarTrigger aria-label={t('toggleSidebar')} className="cursor-pointer" />
      <Separator orientation="vertical" className="mx-1 h-4" />
      <div className="flex-1" />
    </header>
  )
}
