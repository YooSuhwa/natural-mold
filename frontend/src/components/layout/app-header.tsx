'use client'

import { SidebarTrigger } from '@/components/ui/sidebar'
import { BreadcrumbNav } from '@/components/layout/breadcrumb-nav'

export function AppHeader() {
  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b px-4">
      {/* Mobile only: sidebar trigger (desktop uses sidebar's built-in toggle) */}
      <SidebarTrigger className="md:hidden cursor-pointer" />
      <BreadcrumbNav />
    </header>
  )
}
