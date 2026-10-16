'use client'

import { Building2Icon, CheckIcon, ChevronsUpDownIcon } from 'lucide-react'
import { useTranslations } from 'next-intl'
import { toast } from 'sonner'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
} from '@/components/ui/sidebar'
import { useActiveOrganization, useSwitchActiveOrganization } from '@/lib/hooks/use-organizations'

export function OrganizationSwitcher() {
  const t = useTranslations('sidebar.orgSwitcher')
  const { organizations, activeOrganization } = useActiveOrganization()
  const switchOrganization = useSwitchActiveOrganization()

  if (!activeOrganization) return null

  function selectOrganization(orgId: string) {
    if (switchOrganization.isPending || orgId === activeOrganization?.id) return
    switchOrganization.mutate(orgId, {
      onError: () => toast.error(t('switchFailed')),
    })
  }

  const triggerLabel = t('trigger', { name: activeOrganization.name })

  return (
    <SidebarGroup className="py-0">
      <SidebarGroupContent>
        <SidebarMenu>
          <SidebarMenuItem>
            <DropdownMenu>
              <DropdownMenuTrigger
                render={<SidebarMenuButton tooltip={triggerLabel} />}
                aria-label={triggerLabel}
                disabled={switchOrganization.isPending}
                data-testid="organization-switcher-trigger"
              >
                <Building2Icon className="size-4" />
                <span className="flex min-w-0 flex-1 flex-col text-left">
                  <span className="truncate text-sm font-semibold">{activeOrganization.name}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {activeOrganization.tenant_name}
                  </span>
                </span>
                <ChevronsUpDownIcon className="size-4 text-muted-foreground" />
              </DropdownMenuTrigger>
              <DropdownMenuContent side="bottom" align="start" className="w-64">
                <DropdownMenuGroup>
                  <DropdownMenuLabel>{t('label')}</DropdownMenuLabel>
                  {organizations.map((org) => {
                    const isActive = org.id === activeOrganization.id
                    const isUnavailable = org.tenant_status !== 'active'
                    return (
                      <DropdownMenuItem
                        key={org.id}
                        disabled={isUnavailable}
                        onClick={() => selectOrganization(org.id)}
                        className="justify-between"
                      >
                        <span className="flex min-w-0 flex-col">
                          <span className="truncate">{org.name}</span>
                          <span className="truncate text-xs text-muted-foreground">
                            {isUnavailable ? t('unavailable') : org.tenant_name}
                          </span>
                        </span>
                        {isActive ? (
                          <CheckIcon
                            className="size-4 text-primary-strong"
                            aria-label={t('current')}
                          />
                        ) : (
                          <span className="size-4" />
                        )}
                      </DropdownMenuItem>
                    )
                  })}
                </DropdownMenuGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          </SidebarMenuItem>
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}
