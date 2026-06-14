'use client'

import type { ReactNode } from 'react'
import { usePathname } from 'next/navigation'
import { SidebarProvider, SidebarInset } from '@/components/ui/sidebar'
import { TooltipProvider } from '@/components/ui/tooltip'
import { QueryProvider } from '@/lib/providers/query-provider'
import { AppSidebar } from '@/components/layout/app-sidebar'
import { SettingsSidebar } from '@/components/layout/settings-sidebar'
import { AppHeader } from '@/components/layout/app-header'
import { OnboardingDialog } from '@/components/auth/OnboardingDialog'

/** Routes that must render bare (no sidebar/header) — public visitor pages. */
const BARE_ROUTE_PREFIXES = ['/shared/'] as const
/** Auth pages render their own centered layout — no sidebar/header. */
const BARE_ROUTES = new Set<string>(['/login', '/register'])

export function AppLayout({
  children,
  initialSidebarWidth,
}: {
  children: ReactNode
  initialSidebarWidth?: number | null
}) {
  const pathname = usePathname()
  const isBare =
    (pathname ? BARE_ROUTES.has(pathname) : false) ||
    BARE_ROUTE_PREFIXES.some((prefix) => pathname?.startsWith(prefix))
  const isSettingsRoute = pathname === '/settings' || pathname?.startsWith('/settings/')

  // QueryProvider and TooltipProvider live outside the bare/full conditional so
  // the QueryClient instance survives route transitions (e.g. /login → /).
  // If they were inside each branch, React would unmount+remount them on every
  // auth-state change, destroying the cache and causing a loading flash.
  return (
    <QueryProvider>
      <TooltipProvider>
        {isBare ? (
          children
        ) : (
          <SidebarProvider initialSidebarWidth={initialSidebarWidth}>
            {isSettingsRoute ? <SettingsSidebar /> : <AppSidebar />}
            <SidebarInset>
              <AppHeader />
              <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
              <OnboardingDialog />
            </SidebarInset>
          </SidebarProvider>
        )}
      </TooltipProvider>
    </QueryProvider>
  )
}
