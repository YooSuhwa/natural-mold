'use client'

import type { ReactNode } from 'react'
import { usePathname } from 'next/navigation'
import { SidebarProvider, SidebarInset } from '@/components/ui/sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import { QueryProvider } from '@/lib/providers/query-provider'
import { AppSidebar } from '@/components/layout/app-sidebar'
import { AppHeader } from '@/components/layout/app-header'

/** Routes that must render bare (no sidebar/header) — public visitor pages. */
const BARE_ROUTE_PREFIXES = ['/shared/'] as const

export function AppLayout({ children }: { children: ReactNode }) {
  const pathname = usePathname()
  const isBare = BARE_ROUTE_PREFIXES.some((prefix) => pathname?.startsWith(prefix))

  if (isBare) {
    return (
      <QueryProvider>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryProvider>
    )
  }

  return (
    <QueryProvider>
      <TooltipProvider>
        <SidebarProvider>
          <AppSidebar />
          <SidebarInset>
            <AppHeader />
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
          </SidebarInset>
        </SidebarProvider>
      </TooltipProvider>
    </QueryProvider>
  )
}
