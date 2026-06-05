'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useTranslations } from 'next-intl'
import {
  Code2Icon,
  HistoryIcon,
  KeyRoundIcon,
  MonitorCogIcon,
  ShieldCheckIcon,
  SlidersHorizontalIcon,
  StoreIcon,
  UsersRoundIcon,
  UserIcon,
} from 'lucide-react'
import type { ComponentType, ReactNode, SVGProps } from 'react'

import { PageHeader } from '@/components/shared/page-header'
import { useSession } from '@/lib/auth/session'
import { cn } from '@/lib/utils'

interface SettingsShellProps {
  children: ReactNode
}

type SettingsNavItem = {
  href: string
  label: string
  icon: ComponentType<SVGProps<SVGSVGElement>>
}

type SettingsNavSection = {
  label: string
  items: SettingsNavItem[]
}

function isActive(pathname: string, href: string) {
  if (href === '/settings') return pathname === href
  return pathname === href || pathname.startsWith(`${href}/`)
}

export function SettingsShell({ children }: SettingsShellProps) {
  const pathname = usePathname()
  const t = useTranslations('appSettings')
  const { data: user } = useSession()

  const sections: SettingsNavSection[] = [
    {
      label: t('nav.account'),
      items: [
        { href: '/settings', label: t('nav.profile'), icon: UserIcon },
        { href: '/settings/security', label: t('nav.security'), icon: ShieldCheckIcon },
        { href: '/settings/appearance', label: t('nav.appearance'), icon: MonitorCogIcon },
        { href: '/settings/audit', label: t('nav.audit'), icon: HistoryIcon },
      ],
    },
    {
      label: t('nav.api'),
      items: [
        { href: '/settings/agent-api', label: t('nav.agentApi'), icon: Code2Icon },
      ],
    },
    ...(user?.is_super_user
      ? [
          {
            label: t('nav.admin'),
            items: [
              {
                href: '/settings/marketplace-admin',
                label: t('nav.marketplaceAdmin'),
                icon: StoreIcon,
              },
              {
                href: '/settings/system-credentials',
                label: t('nav.systemCredentials'),
                icon: KeyRoundIcon,
              },
              {
                href: '/settings/system-llm',
                label: t('nav.systemLlm'),
                icon: SlidersHorizontalIcon,
              },
              {
                href: '/settings/admin/audit',
                label: t('nav.adminAudit'),
                icon: UsersRoundIcon,
              },
            ],
          },
        ]
      : []),
  ]

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-auto p-6">
      <PageHeader title={t('title')} description={t('description')} />

      <div className="mx-auto grid w-full max-w-5xl gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        <aside className="moldy-panel h-fit p-2">
          <nav className="space-y-4" aria-label={t('navLabel')}>
            {sections.map((section) => (
              <div key={section.label} className="space-y-1">
                <p className="px-2 py-1 moldy-ui-micro font-semibold uppercase text-muted-foreground">
                  {section.label}
                </p>
                <div className="space-y-1">
                  {section.items.map((item) => {
                    const active = isActive(pathname, item.href)
                    const Icon = item.icon
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        aria-current={active ? 'page' : undefined}
                        className={cn(
                          'flex h-8 items-center gap-2 rounded-lg px-2 text-sm font-medium transition-colors',
                          active
                            ? 'bg-primary/15 text-primary-strong'
                            : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                        )}
                      >
                        <Icon className="size-4" aria-hidden />
                        <span>{item.label}</span>
                      </Link>
                    )
                  })}
                </div>
              </div>
            ))}
          </nav>
        </aside>

        <main className="min-w-0">{children}</main>
      </div>
    </div>
  )
}
