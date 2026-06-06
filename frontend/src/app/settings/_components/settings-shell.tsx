'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useTranslations } from 'next-intl'
import {
  BarChart3Icon,
  BrainIcon,
  CalendarClockIcon,
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
import { useTriggerSummary } from '@/lib/hooks/use-triggers'
import { cn } from '@/lib/utils'

interface SettingsShellProps {
  children: ReactNode
  wide?: boolean
}

type SettingsNavItem = {
  href: string
  label: string
  icon: ComponentType<SVGProps<SVGSVGElement>>
  badge?: number
}

type SettingsNavSection = {
  label: string
  items: SettingsNavItem[]
}

function isActive(pathname: string, href: string) {
  if (href === '/settings') return pathname === href
  return pathname === href || pathname.startsWith(`${href}/`)
}

export function SettingsShell({ children, wide = false }: SettingsShellProps) {
  const pathname = usePathname()
  const t = useTranslations('appSettings')
  const { data: user } = useSession()
  const { data: triggerSummary } = useTriggerSummary()

  const sections: SettingsNavSection[] = [
    {
      label: t('nav.account'),
      items: [
        { href: '/settings', label: t('nav.profile'), icon: UserIcon },
        { href: '/settings/memory', label: t('nav.memory'), icon: BrainIcon },
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
    {
      label: t('nav.resources'),
      items: [
        { href: '/settings/credentials', label: t('nav.credentials'), icon: KeyRoundIcon },
        { href: '/settings/models', label: t('nav.models'), icon: BrainIcon },
        {
          href: '/settings/schedules',
          label: t('nav.schedules'),
          icon: CalendarClockIcon,
          badge: triggerSummary?.total_unread ?? 0,
        },
        { href: '/settings/usage', label: t('nav.usage'), icon: BarChart3Icon },
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

      <div
        className={cn(
          'mx-auto grid w-full gap-6 lg:grid-cols-[220px_minmax(0,1fr)]',
          wide ? 'max-w-7xl' : 'max-w-5xl',
        )}
      >
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
                        {item.badge ? (
                          <span className="ml-auto rounded-md bg-primary px-1.5 py-0.5 moldy-ui-caption font-semibold leading-none text-primary-foreground ring-1 ring-primary-strong/15">
                            {item.badge > 99 ? '99+' : item.badge}
                          </span>
                        ) : null}
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
